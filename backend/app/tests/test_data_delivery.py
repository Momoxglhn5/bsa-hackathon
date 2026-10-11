"""Sized source quotes, missing USD prices and cache-only API contracts."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters.pendle_api import RawResponse
from app.adapters.pendle_execution import PendleQuoteClient, SIMULATION_RECEIVER, raw_amount
from app.ingestion.incidents import normalize_catalog
from app.ingestion.prices import enrich_market_prices
from app.main import create_app
from app.models.market import TokenRef
from app.tests.test_stale_errors import morpho_market, NOW, PT, LOAN, PENDLE


def route_payload(amount="1000000000000000000", output="987654"):
    return {"inputs": [{"token": PT, "amount": amount}], "routes": [{
        "contractParamInfo": {"method": "swapExactPtForToken",
            "contractCallParamsName": ["receiver", "market", "exactPtIn", "output"],
            "contractCallParams": [SIMULATION_RECEIVER, PENDLE, amount,
                                   {"tokenOut": LOAN, "minTokenOut": "970000"}]},
        "outputs": [{"token": LOAN, "amount": output}],
        "data": {"aggregatorType": "KYBERSWAP", "priceImpact": Decimal("-0.012345"), "gasUsed": "123456"},
        "tx": {"data": "PRIVATE_UNSIGNED_CALLDATA_NOT_FOR_API"}}]}


def client_for(payload, code=201):
    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/core/v3/sdk/1/convert"
        return httpx.Response(code, text=json.dumps(payload, default=str))
    return PendleQuoteClient(transport=httpx.MockTransport(handler))


def call_quote(client):
    return client.quote(market=PENDLE, collateral=TokenRef(chain_id=1, address=PT, decimals=18),
        loan=TokenRef(chain_id=1, address=LOAN, decimals=6), amount=Decimal("1"))


def test_exact_token_precision_and_no_zero_fill():
    assert raw_amount(Decimal("1.234567"), 6) == 1234567
    with pytest.raises(ValueError, match="precision"):
        raw_amount(Decimal("1.2345678"), 6)
    for value in ("0", "-1", "NaN"):
        with pytest.raises(ValueError):
            raw_amount(value, 18)


def test_sized_quote_uses_actual_output_with_proof_and_discards_transaction():
    with client_for(route_payload()) as client:
        quote, evidence, details, raw = call_quote(client)
    assert quote.output_amount == Decimal("0.987654")
    assert quote.value == Decimal("0.987654")
    assert quote.price_impact == Decimal("-0.012345")
    assert evidence.payload_sha256 == hashlib.sha256(raw).hexdigest()
    assert quote.evidence_ids == (evidence.evidence_id,)
    assert details["gas_used_estimate"] == "123456"
    assert "tx" not in details and "tx" not in quote.model_dump()
    assert "calldata" not in quote.model_dump_json().lower()


@pytest.mark.parametrize("mismatch", ["market", "amount", "output", "min_output"])
def test_wrong_market_amount_or_output_cannot_become_a_quote(mismatch):
    payload = route_payload()
    route = payload["routes"][0]
    if mismatch == "market":
        route["contractParamInfo"]["contractCallParams"][1] = "0x" + "77" * 20
    elif mismatch == "amount":
        payload["inputs"][0]["amount"] = "2000000000000000000"
    elif mismatch == "output":
        route["outputs"][0]["token"] = PT
    else:
        route["contractParamInfo"]["contractCallParams"][3]["minTokenOut"] = "999999"
    with client_for(payload) as client, pytest.raises(ValueError):
        call_quote(client)


def test_insufficient_liquidity_is_unavailable_not_zero_price():
    with client_for({"message": "MarketProportionTooHigh"}, 400) as client:
        with pytest.raises(ValueError, match="HTTP 400.*MarketProportionTooHigh"):
            call_quote(client)


def test_usd_prices_keep_rpc_timestamp_and_missing_loan_is_none():
    timestamp = NOW + timedelta(minutes=2)
    raw = RawResponse(source_id="pendle_api", operation="rest:/v1/prices/assets", url="https://api-v2.pendle.finance/core/v1/prices/assets",
        request_params={"ids": f"1-{PT},1-{LOAN}"}, status_code=200, requested_at=timestamp,
        retrieved_at=timestamp, payload={"prices": {f"1-{PT}": Decimal("0.98")}},
        payload_sha256="a" * 64, attempts=1, rate_limit={}, raw_body=b"{}")
    client = SimpleNamespace(fetch_asset_prices=lambda *args: SimpleNamespace(responses=[raw], errors=[]))
    enriched, evidence, errors = enrich_market_prices([morpho_market(NOW)], client)
    assert enriched[0].as_of == NOW
    assert enriched[0].morpho.collateral_price.observed_at == timestamp
    assert enriched[0].morpho.collateral_price.quote_unit == "USD"
    assert enriched[0].morpho.loan_price is None
    assert enriched[0].morpho.collateral_price.evidence_ids == (raw.evidence_id,)
    assert errors == [f"USD price missing: {LOAN}"]


def test_incident_catalog_filters_future_and_never_creates_probabilities():
    body = json.dumps([{"name": "Penpie", "date": int(NOW.timestamp()) - 86400, "amount": 27000000},
                       {"name": "Future incident", "date": int(NOW.timestamp()) + 86400}]).encode()
    data = normalize_catalog(body, NOW)
    assert len(data["events"]) == 1
    assert data["related_events"][0]["matches_selected_market"] is None
    assert data["probability"] is None


@pytest.mark.parametrize("inherit_httpx_post", [False, True])
def test_cached_quote_api_handles_exact_amount_unknown_amount_and_expiry(tmp_path, monkeypatch, inherit_httpx_post):
    with client_for(route_payload()) as client:
        quote, evidence, details, _ = call_quote(client)
    strategy = "strat_" + "a" * 24
    bundle = tmp_path / "delivery" / "snapshot"
    bundle.mkdir(parents=True)
    (bundle.parent / "current.json").write_text(json.dumps({"bundle": bundle.name}))
    (bundle / "exit_quotes.json").write_text(json.dumps({"records": [{"strategy_id": strategy,
        "input_amount": "1", "status": "PARTIAL", "quote": quote.model_dump(mode="json"),
        "missing_fields": ["closing_feasible"]}]}))
    def forbid_network(*args, **kwargs):
        raise AssertionError("A cached API must not fetch providers")
    # Block outbound HTTP without intercepting TestClient's in-process requests.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbid_network)
    if inherit_httpx_post:
        # Exercise versions where TestClient dispatches through the base method.
        monkeypatch.setattr(TestClient, "post", TestClient.__mro__[1].post)
    api = TestClient(create_app(tmp_path / "db.sqlite", clock=lambda: quote.observed_at))
    fresh = api.post("/api/v1/exit-quotes", json={"strategy_id": strategy, "input_amount": "1.00"}).json()
    assert fresh["status"] == "PARTIAL"
    assert fresh["missing_fields"] == ["closing_feasible"]
    unknown = api.post("/api/v1/exit-quotes", json={"strategy_id": strategy, "input_amount": "2"}).json()
    assert unknown["status"] == "MISSING" and unknown["data"]["quote"] is None
    api.app.state.clock = lambda: quote.valid_until
    assert api.post("/api/v1/exit-quotes", json={"strategy_id": strategy, "input_amount": "1"}).json()["status"] == "STALE"


def test_simulated_usd_sizing_requires_fresh_prices_and_keeps_exact_token_units():
    import importlib.util
    from pathlib import Path
    from app.ingestion.normalize import build_market_price, SourceRef
    from app.models.market import MarketDTO
    path = Path(__file__).resolve().parents[2] / "scripts" / "finish_data.py"
    spec = importlib.util.spec_from_file_location("finish_data", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = morpho_market(NOW).model_dump()
    data["morpho"]["collateral_token"]["decimals"] = 18
    data["morpho"]["loan_token"]["decimals"] = 6
    market = MarketDTO.model_validate(data)
    ref = SourceRef("pendle_api", "ev_" + "a" * 24, NOW)
    data = market.model_dump()
    data["morpho"]["collateral_price"] = build_market_price(market.morpho.collateral_token,
        "0.98", quote_unit="USD", pricing_method="API_SPOT", ref=ref)
    data["morpho"]["loan_price"] = build_market_price(market.morpho.loan_token,
        "0.999", quote_unit="USD", pricing_method="API_SPOT", ref=ref)
    market = MarketDTO.model_validate(data)
    strategy = SimpleNamespace(strategy_id="strat_" + "a" * 24)
    position = module.position_for_size(market, strategy, Decimal("1000000"), NOW)
    assert position.kind == "SIMULATED" and position.status == "UNVERIFIED"
    assert position.collateral_amount == Decimal("1020408.163265306122448979")
    assert position.borrow_amount == Decimal("700700.700700")
    with pytest.raises(module.SizingUnavailable, match="stale"):
        module.position_for_size(market, strategy, Decimal("1000000"), NOW + timedelta(seconds=301))


@pytest.mark.parametrize("fault", ["amount", "validity", "missing_fields", "future"])
def test_cached_quote_rejects_invalid_delivery_or_future_observation(tmp_path, fault):
    with client_for(route_payload()) as client:
        quote, _, _, _ = call_quote(client)
    strategy = "strat_" + "a" * 24
    record = {"strategy_id": strategy, "input_amount": "1",
              "quote": quote.model_dump(mode="json"), "missing_fields": ["closing_feasible"]}
    if fault == "amount":
        record["quote"]["input_amount"] = "2"
    elif fault == "validity":
        record["quote"]["valid_until"] = None
    elif fault == "missing_fields":
        del record["missing_fields"]
    bundle = tmp_path / "delivery" / "snapshot"
    bundle.mkdir(parents=True)
    (bundle.parent / "current.json").write_text(json.dumps({"bundle": bundle.name}))
    (bundle / "exit_quotes.json").write_text(json.dumps({"records": [record]}))
    now = quote.observed_at - timedelta(seconds=1) if fault == "future" else quote.observed_at
    api = TestClient(create_app(tmp_path / "db.sqlite", clock=lambda: now))
    response = api.post("/api/v1/exit-quotes", json={"strategy_id": strategy, "input_amount": "1"})
    if fault == "future":
        assert response.status_code == 200 and response.json()["status"] == "STALE"
    else:
        assert response.status_code == 503
