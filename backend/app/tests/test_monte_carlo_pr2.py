"""Monte Carlo PR2: Data mapping and oracle-series calibration.

Every fixture here is SYNTHETIC. Addresses, blocks, hashes, prices and RPC
evidence imitate the structure of real Data records; none was read on-chain.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import json
import math
import statistics

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.adapters.ethereum_rpc import MORPHO_BLUE_ADDRESS, PinnedBlock, RpcCallResult, RpcTransportError
from app.analytics.dataset import AnalyticsSnapshot
from app.analytics.provider import snapshot_from_store
from app.ingestion.normalize import SourceRef
from app.ingestion.oracle_history import block_at_or_before, run_oracle_backfill
from app.main import create_app
from app.models.evidence import (
    DataMode, DataStatus, EvidenceDTO, SourceKind, VerificationCheckDTO, VerificationStatus,
    make_evidence_id, scale_raw,
)
from app.models.market import (
    MarketDTO, MarketPriceDTO, MorphoMarketFields, OraclePriceDTO, PricingMethod, Protocol, QuoteUnit, TokenRef,
)
from app.models.position import PositionDTO, PositionKind
from app.models.strategy import StrategyDTO, StrategyStatus, make_strategy_id
from app.models.timeseries import SeriesUnit, SubjectKind, TimeseriesDTO, TimeseriesMetric, TimeseriesPointDTO
from app.storage.parquet_store import Observation, ParquetStore
from app.storage.sqlite_store import SqliteStore
from app.stress.monte_carlo import run_monte_carlo
from app.stress.monte_carlo.adapter import (
    MonteCarloDataRequest, MonteCarloDataUnavailable, build_monte_carlo_request,
)
from app.stress.monte_carlo.calibration import (
    calibrate_oracle_volatility, sampling_targets, select_oracle_series,
)
from app.stress.monte_carlo.models import InputScope, McStatus, MonteCarloRequest, SourceMode

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
MARKET_ID = "0x" + "ab" * 32
OTHER_MARKET_ID = "0x" + "ef" * 32
PT, LOAN, OTHER = "0x" + "11" * 20, "0x" + "22" * 20, "0x" + "99" * 20
PENDLE, ORACLE, IRM = "0x" + "33" * 20, "0x" + "44" * 20, "0x" + "55" * 20
OWNER = "0x" + "66" * 20
BLOCK = 26_000_000
EXPONENT = 36 + 6 - 18  # Morpho oracle scale for a 6-decimal loan token and 18-decimal PT
HASH = "0x" + "cd" * 32
PT_TOKEN = TokenRef(chain_id=1, address=PT, symbol="PT-SYNTH", decimals=18)
LOAN_TOKEN = TokenRef(chain_id=1, address=LOAN, symbol="USDC", decimals=6)
LOW, HIGH = Decimal("0.95"), Decimal("0.96")


def raw(value: Decimal) -> int:
    return int(value * 10**EXPONENT)


def rpc_evidence(target, block, method, *, fetched_at, excerpt=None, mode=DataMode.REAL):
    return EvidenceDTO(
        chain_id=1, data_mode=mode, evidence_id=make_evidence_id("ethereum_rpc", f"{method}@{target}", {}, block),
        source_kind=SourceKind.RPC, source_id="ethereum_rpc", method=method, target_address=target,
        block_number=block, block_hash=HASH, fetched_at=fetched_at, status=DataStatus.OK,
        decoded_excerpt=excerpt, source_uri="synthetic.invalid",
    )


def price_evidence(block, value, fetched_at=NOW - timedelta(hours=1), target=ORACLE):
    return rpc_evidence(target, block, "eth_call price()", fetched_at=fetched_at, excerpt={"out0": str(raw(value))})


def history(days=31, end=NOW, values=None):
    """Alternating synthetic oracle prices, one point 12 s before each UTC midnight."""
    points, evidence = [], []
    targets = sampling_targets(end)[-days:]
    for k, target in enumerate(targets):
        value = (values or {}).get(k, LOW if k % 2 == 0 else HIGH)
        block = BLOCK - 7200 * (len(targets) - k)
        ev = price_evidence(block, value)
        evidence.append(ev)
        points.append(TimeseriesPointDTO(timestamp=target - timedelta(seconds=12), value=scale_raw(raw(value), EXPONENT),
                                         status=DataStatus.OK, block_number=block, evidence_ids=(ev.evidence_id,)))
    return points, evidence


def oracle_series(points, **changes):
    fields = dict(chain_id=1, data_mode=DataMode.REAL, series_id="ts_oracle_price_synthetic",
                  metric=TimeseriesMetric.ORACLE_PRICE, subject_kind=SubjectKind.MARKET, subject_id=MARKET_ID,
                  unit=SeriesUnit.TOKEN, quote_asset=LOAN_TOKEN, pricing_method=PricingMethod.MORPHO_ORACLE,
                  source_id="ethereum_rpc", status=DataStatus.OK, points=tuple(points))
    fields.update(changes)
    return TimeseriesDTO(**fields)


@dataclass
class World:
    market: MarketDTO
    strategy: StrategyDTO
    simulated: PositionDTO
    onchain: PositionDTO
    evidence: list
    series: list

    def snapshot(self, **changes):
        fields = dict(snapshot_id="synthetic-pr2", as_of=NOW, description="SYNTHETIC PR2 test world",
                      markets=(self.market,), strategies=(self.strategy,),
                      positions=(self.simulated, self.onchain), evidence=tuple(self.evidence),
                      series=tuple(self.series))
        fields.update(changes)
        return AnalyticsSnapshot(**fields)


def world(*, oracle_value=Decimal("0.955"), oracle_observed_at=NOW - timedelta(seconds=60),
          oracle=True, history_days=31) -> World:
    params_ev = rpc_evidence(MORPHO_BLUE_ADDRESS, BLOCK, "eth_call idToMarketParams(bytes32)",
                             fetched_at=NOW - timedelta(seconds=60))
    oracle_ev = price_evidence(BLOCK, oracle_value, fetched_at=NOW - timedelta(seconds=60))
    position_ev = rpc_evidence(MORPHO_BLUE_ADDRESS, BLOCK, "eth_call position(bytes32,address)",
                               fetched_at=NOW - timedelta(seconds=60))
    check = VerificationCheckDTO(check="morpho.params", claimed_value=PT, claimed_source_id="ethereum_rpc",
                                 onchain_value=PT, status=VerificationStatus.VERIFIED,
                                 evidence_ids=(params_ev.evidence_id,))
    price = OraclePriceDTO(
        chain_id=1, value=scale_raw(raw(oracle_value), EXPONENT), base_asset=PT_TOKEN, quote_unit=QuoteUnit.TOKEN,
        quote_asset=LOAN_TOKEN, pricing_method=PricingMethod.MORPHO_ORACLE, observed_at=oracle_observed_at,
        block_number=BLOCK, source_id="ethereum_rpc", evidence_ids=(oracle_ev.evidence_id,), status=DataStatus.OK,
        oracle_address=ORACLE, raw_value=raw(oracle_value), scale_exponent=EXPONENT,
    ) if oracle else None
    market = MarketDTO(
        chain_id=1, data_mode=DataMode.REAL, protocol=Protocol.MORPHO_BLUE, market_id=MARKET_ID,
        status=DataStatus.PARTIAL, as_of=NOW - timedelta(seconds=60), block_number=BLOCK,
        morpho=MorphoMarketFields(
            morpho_address=MORPHO_BLUE_ADDRESS, loan_token=LOAN_TOKEN, collateral_token=PT_TOKEN,
            oracle_address=ORACLE, irm_address=IRM, lltv=Decimal("0.915"), lltv_raw=915 * 10**15,
            # Market totals must never become the position's debt.
            total_borrow_assets=Decimal("98765432"), oracle_price=price,
        ),
        verification=(check,), source_ids=("ethereum_rpc",), evidence_ids=(params_ev.evidence_id,),
    )
    strategy = StrategyDTO(
        chain_id=1, data_mode=DataMode.REAL, strategy_id=make_strategy_id(1, MARKET_ID, PT),
        status=StrategyStatus.VERIFIED_ACTIVE, data_status=DataStatus.OK, morpho_market_id=MARKET_ID,
        pendle_market_address=PENDLE, pt=PT_TOKEN, loan_asset=LOAN_TOKEN, expiry=NOW + timedelta(days=90),
        as_of=NOW - timedelta(hours=1), block_number=BLOCK - 300, verification=(check,),
        source_ids=("ethereum_rpc",), evidence_ids=(params_ev.evidence_id,),
    )
    common = dict(chain_id=1, data_mode=DataMode.REAL, market_id=MARKET_ID, collateral_asset=PT_TOKEN,
                  loan_asset=LOAN_TOKEN, strategy_id=strategy.strategy_id, as_of=NOW - timedelta(seconds=60),
                  status=DataStatus.OK)
    simulated = PositionDTO(position_id="pos_simulated_fund", kind=PositionKind.SIMULATED,
                            collateral_amount=Decimal("2000000"), borrow_amount=Decimal("1200000"),
                            source_ids=("user_input",), **common)
    onchain = PositionDTO(position_id="pos_onchain_owner", kind=PositionKind.ONCHAIN, owner=OWNER,
                          block_number=BLOCK, collateral_amount=Decimal("1000"), borrow_amount=Decimal("700"),
                          source_ids=("ethereum_rpc",), evidence_ids=(position_ev.evidence_id,), **common)
    points, hist_ev = history(history_days)
    series = [oracle_series(points)] if history_days else []
    return World(market, strategy, simulated, onchain, [params_ev, oracle_ev, position_ev, *hist_ev], series)


def settings(position, source=SourceMode.OBSERVED_ORACLE_SERIES, **changes):
    fields = dict(strategy_id=make_strategy_id(1, MARKET_ID, PT), position_id=position.position_id,
                  horizon_days=7, num_paths=2000, seed=7, volatility_source=source)
    if source == SourceMode.USER_ASSUMPTION:
        fields["annual_volatility"] = "0.8"
    fields.update(changes)
    return MonteCarloDataRequest.model_validate(fields)


def build(w, position=None, *, source=SourceMode.OBSERVED_ORACLE_SERIES, evidence=None, series=None,
          market=None, strategy=None, as_of=NOW, **changes):
    position = position or w.onchain
    with localcontext() as ctx:
        ctx.prec = 60
        return build_monte_carlo_request(
            position, market or w.market, strategy or w.strategy, settings=settings(position, source, **changes),
            as_of=as_of, evidence=w.evidence if evidence is None else evidence,
            series=w.series if series is None else series,
        )


def missing_of(callable_):
    with pytest.raises(MonteCarloDataUnavailable) as exc:
        callable_()
    return exc.value.missing_fields


def expected_annual_volatility() -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 60
        x = (HIGH / LOW).ln()
        return x * (Decimal(30) / Decimal(29)).sqrt() * Decimal(365).sqrt()


# -- mapping -----------------------------------------------------------------

def test_dto_mapping_uses_position_amounts_not_market_totals():
    w = world()
    request = build(w, w.simulated, source=SourceMode.USER_ASSUMPTION)
    assert request.collateral_quantity_pt == w.simulated.collateral_amount
    assert request.debt_loan_units == w.simulated.borrow_amount != w.market.morpho.total_borrow_assets
    assert request.lltv == w.market.morpho.lltv
    assert request.initial_oracle_price_loan_per_pt == w.market.morpho.oracle_price.value
    assert request.pt_expiry == w.strategy.expiry
    assert request.loan_token_address == LOAN and request.loan_token_symbol == "USDC"
    assert request.simulation.source_mode == SourceMode.USER_ASSUMPTION
    assert request.simulation.calibration is None


def test_simulated_position_stays_hypothetical_even_with_real_calibration():
    w = world()
    request = build(w, w.simulated)
    assert request.input_scope == InputScope.HYPOTHETICAL
    assert request.simulation.source_mode == SourceMode.OBSERVED_ORACLE_SERIES
    assert "HYPOTHETICAL_POSITION_AMOUNTS" in request.warnings
    result = run_monte_carlo(request)
    assert "HYPOTHETICAL_INPUT_SCOPE" in result.warnings
    assert "SYNTHETIC_FIXTURE_NOT_LIVE" not in result.warnings


def test_onchain_position_with_verified_calibration_is_real_data():
    w = world()
    request = build(w)
    assert request.input_scope == InputScope.REAL_DATA
    calibration = request.simulation.calibration
    assert set(calibration.evidence_ids) <= set(request.evidence_ids)
    result = run_monte_carlo(request)
    assert result.status == McStatus.OK
    assert result.provenance.calibration == SourceMode.OBSERVED_ORACLE_SERIES
    assert result.provenance.oracle_calibration == calibration
    assert {"HISTORICAL_CALIBRATION_NOT_A_FORECAST", "ZERO_DRIFT_ASSUMPTION",
            "NOT_A_REAL_WORLD_LIQUIDATION_PROBABILITY"} <= set(result.warnings)
    assert result.data.realized_liquidation_loss_usd is None
    text = json.dumps(result.model_dump(mode="json"), allow_nan=False)
    assert "NaN" not in text and "Infinity" not in text


@pytest.mark.parametrize("change, message", [
    (lambda w: (w.onchain.model_copy(update={"collateral_asset": TokenRef(chain_id=1, address=OTHER)}),
                {}), "PT"),
    (lambda w: (w.onchain.model_copy(update={"market_id": OTHER_MARKET_ID}), {}), "market IDs"),
    (lambda w: (w.onchain.model_copy(update={"chain_id": 10}), {}), "mainnet"),
    (lambda w: (w.onchain.model_copy(update={"loan_asset": TokenRef(chain_id=1, address=OTHER, symbol="X")}),
                {}), "Loan asset"),
    (lambda w: (w.onchain.model_copy(update={"data_mode": DataMode.DEMO}), {}), "REAL and DEMO"),
    (lambda w: (w.onchain, {"strategy": w.strategy.model_copy(update={"strategy_id": "strat_" + "0" * 24})}),
     "strategy/position"),
])
def test_identity_mismatches_are_rejected(change, message):
    w = world()
    position, extra = change(w)
    with pytest.raises(ValueError, match=message):
        build(w, position, **extra)


def test_unknown_token_symbols_are_not_required():
    # Regression: real Data rows carry token addresses but often no symbol.
    w = world()
    bare = TokenRef(chain_id=1, address=LOAN, decimals=6)
    position = w.simulated.model_copy(update={"loan_asset": bare})
    request = build(w, position, source=SourceMode.USER_ASSUMPTION)
    assert request.loan_token_symbol is None and request.loan_token_address == LOAN
    assert run_monte_carlo(request).status == McStatus.OK
    with pytest.raises(ValidationError, match="symbol or an address"):
        MonteCarloRequest.model_validate(request.model_dump(mode="json") | {"loan_token_address": None})


def test_other_chain_rejected_at_contract_boundary():
    with pytest.raises(ValidationError):
        TokenRef(chain_id=8453, address=PT)


# -- current oracle and evidence ------------------------------------------------

@pytest.mark.parametrize("kwargs, field", [
    ({"oracle": False}, "market.morpho.oracle_price"),
    ({"oracle_observed_at": NOW - timedelta(minutes=10)}, "market.morpho.oracle_price.observed_at"),
    ({"oracle_observed_at": NOW + timedelta(minutes=1)}, "market.morpho.oracle_price.observed_at"),
])
def test_current_oracle_absent_stale_or_future(kwargs, field):
    w = world(**kwargs)
    assert field in missing_of(lambda: build(w, w.simulated, source=SourceMode.USER_ASSUMPTION))


def test_missing_or_mismatched_oracle_proof():
    w = world()
    oracle_id = w.market.morpho.oracle_price.evidence_ids[0]
    without = [e for e in w.evidence if e.evidence_id != oracle_id]
    missing = missing_of(lambda: build(w, evidence=without))
    assert {"evidence:" + oracle_id, "oracle.rpc_evidence"} <= set(missing)
    wrong_target = [e.model_copy(update={"target_address": OTHER}) if e.evidence_id == oracle_id else e
                    for e in w.evidence]
    assert "oracle.rpc_evidence" in missing_of(lambda: build(w, evidence=wrong_target))


@pytest.mark.parametrize("changes", [
    {"method": "eth_call decimals()"},
    {"source_id": "other_rpc"},
    {"decoded_excerpt": None},
    {"decoded_excerpt": {"out0": "-1"}},
    {"decoded_excerpt": {"out0": "1.5"}},
    {"decoded_excerpt": {"out0": str(raw(Decimal("0.1")))}},
])
def test_current_oracle_requires_matching_method_and_raw_value(changes):
    w = world()
    oracle_id = w.market.morpho.oracle_price.evidence_ids[0]
    evidence = [e.model_copy(update=changes) if e.evidence_id == oracle_id else e
                for e in w.evidence]
    assert "oracle.rpc_evidence" in missing_of(lambda: build(w, evidence=evidence))


def test_changed_current_oracle_dto_cannot_use_the_original_proof(monkeypatch):
    w = world()
    data = w.market.model_dump()
    data["morpho"]["oracle_price"].update(value=Decimal("0.1"), raw_value=raw(Decimal("0.1")))
    market = MarketDTO.model_validate(data)
    snapshot = w.snapshot().model_copy(update={"markets": (market,)})
    client = TestClient(create_app(snapshot=snapshot, clock=lambda: NOW, replay=False))
    def no_simulation(*args, **kwargs):
        raise AssertionError("Unproven current price must not reach simulation")
    monkeypatch.setattr("app.api.routes_analytics.run_monte_carlo", no_simulation)
    response = post(client, w.onchain)
    assert response.status_code == 200
    assert response.json()["status"] == "UNAVAILABLE"
    assert "oracle.rpc_evidence" in response.json()["missing_fields"]
    assert response.json()["data"]["data"] is None


def test_unavailable_never_substitutes_fixture():
    w = world(history_days=0)
    missing = missing_of(lambda: build(w))
    assert missing == ["oracle_series"]


# -- calibration -----------------------------------------------------------------

def test_calibration_on_known_series():
    w = world()
    calibration = calibrate_oracle_volatility(w.series[0], w.market, evidence=w.evidence, as_of=NOW)
    assert calibration.observation_count == 31 and calibration.return_count == 30
    assert calibration.window_end == datetime(2026, 10, 10, tzinfo=timezone.utc)
    assert calibration.window_start == calibration.window_end - timedelta(days=30)
    assert abs(calibration.annual_volatility - expected_annual_volatility()) < Decimal("1e-15")
    assert calibration.daily_log_return_mean == 0
    assert calibration.annual_drift == 0 and calibration.drift_policy == "FIXED_ZERO_NOT_ESTIMATED"
    assert len(calibration.evidence_ids) == 31
    # Independent float cross-check of the estimator (sample stdev, sqrt(365)).
    prices = [float(p.oracle_price_loan_per_pt) for p in calibration.points]
    returns = [math.log(b / a) for a, b in zip(prices, prices[1:])]
    assert math.isclose(statistics.stdev(returns) * math.sqrt(365), float(calibration.annual_volatility),
                        rel_tol=1e-12)


def test_history_too_short():
    w = world(history_days=20)
    missing = missing_of(lambda: build(w))
    assert len([m for m in missing if m.startswith("oracle_series.sample:")]) == 11


def test_gap_is_not_filled():
    w = world()
    points = list(w.series[0].points)
    gap = points.pop(10)
    missing = missing_of(lambda: build(w, series=[oracle_series(points)]))
    target = (gap.timestamp + timedelta(seconds=12)).isoformat().replace("+00:00", "Z")
    assert missing == ["oracle_series.sample:" + target]


def test_point_outside_tolerance_is_not_used():
    w = world()
    points = list(w.series[0].points)
    points[5] = points[5].model_copy(update={"timestamp": points[5].timestamp - timedelta(hours=2)})
    assert len(missing_of(lambda: build(w, series=[oracle_series(points)]))) == 1


@pytest.mark.parametrize("bad_point", [
    lambda p: p.model_copy(update={"value": None, "status": DataStatus.MISSING}),
    lambda p: p.model_copy(update={"value": Decimal("0")}),
    lambda p: p.model_copy(update={"value": p.value + Decimal("0.001")}),  # disagrees with its RPC proof
    lambda p: p.model_copy(update={"evidence_ids": ()}),
    lambda p: p.model_copy(update={"block_number": p.block_number + 1}),
])
def test_invalid_or_unproven_point_is_rejected(bad_point):
    w = world()
    points = list(w.series[0].points)
    points[3] = bad_point(points[3])
    missing = missing_of(lambda: build(w, series=[oracle_series(points, status=DataStatus.PARTIAL)]))
    assert len(missing) == 1 and missing[0].startswith("oracle_series.sample:")


def test_valid_earlier_candidate_is_used_when_latest_is_invalid():
    w = world()
    points = list(w.series[0].points)
    late = points[3].model_copy(update={"timestamp": points[3].timestamp + timedelta(seconds=6),
                                        "value": None, "status": DataStatus.ERROR, "evidence_ids": ()})
    points.insert(4, late)
    calibration = build(w, series=[oracle_series(points, status=DataStatus.PARTIAL)]).simulation.calibration
    assert calibration.skipped_candidate_points == 1
    assert calibration.points[3].observed_at == points[3].timestamp


def test_evidence_from_the_future_or_wrong_oracle_is_rejected():
    w = world()
    first = w.series[0].points[0].evidence_ids[0]
    future = [e.model_copy(update={"fetched_at": NOW + timedelta(seconds=1)}) if e.evidence_id == first else e
              for e in w.evidence]
    assert len(missing_of(lambda: build(w, evidence=future))) == 1
    other = [e.model_copy(update={"target_address": OTHER}) if e.evidence_id == first else e for e in w.evidence]
    assert len(missing_of(lambda: build(w, evidence=other))) == 1


def test_future_points_are_ignored_and_counted():
    w = world()
    points, evidence = history(31, end=NOW + timedelta(days=2))
    w.evidence += evidence
    future_world_points = [p for p in points if p.timestamp > NOW]
    series = oracle_series(sorted([*w.series[0].points, *future_world_points], key=lambda p: p.timestamp))
    calibration = calibrate_oracle_volatility(series, w.market, evidence=w.evidence, as_of=NOW)
    assert calibration.ignored_future_points == len(future_world_points) == 2
    assert calibration.window_end <= NOW


def test_decreasing_blocks_rejected():
    w = world()
    points = list(w.series[0].points)
    # Day 8 is proven at a block older than day 7's: a reorg-like or mislabelled history.
    earlier = price_evidence(points[7].block_number - 1, points[8].value)
    w.evidence.append(earlier)
    points[8] = points[8].model_copy(update={"block_number": earlier.block_number,
                                             "evidence_ids": (earlier.evidence_id,)})
    assert missing_of(lambda: build(w, series=[oracle_series(points)])) == ["oracle_series.block_order"]


def test_spot_price_history_is_refused():
    w = world()
    spot = TimeseriesDTO(
        chain_id=1, data_mode=DataMode.REAL, series_id="ts_pt_spot_synthetic", metric=TimeseriesMetric.MARKET_PRICE,
        subject_kind=SubjectKind.ASSET, subject_id=PT, unit=SeriesUnit.USD, pricing_method=PricingMethod.API_SPOT,
        source_id="pendle_api", status=DataStatus.OK, points=w.series[0].points,
    )
    assert missing_of(lambda: build(w, series=[spot])) == ["oracle_series"]
    missing = missing_of(lambda: calibrate_oracle_volatility(spot, w.market, evidence=w.evidence, as_of=NOW))
    assert {"oracle_series.metric", "oracle_series.subject", "oracle_series.source"} <= set(missing)


@pytest.mark.parametrize("changes, field", [
    ({"source_id": "morpho_api"}, "oracle_series.source"),
    ({"data_mode": DataMode.DEMO}, "oracle_series.data_mode"),
    ({"quote_asset": TokenRef(chain_id=1, address=OTHER, decimals=6)}, "oracle_series.quote_asset"),
    ({"subject_id": OTHER_MARKET_ID}, "oracle_series.subject"),
])
def test_series_identity_checks(changes, field):
    w = world()
    series = oracle_series(w.series[0].points, **changes)
    missing = missing_of(lambda: calibrate_oracle_volatility(series, w.market, evidence=w.evidence, as_of=NOW))
    assert field in missing
    if field in ("oracle_series.source", "oracle_series.subject"):
        assert select_oracle_series([series], w.market)[0] is None


def test_seed_reproducibility_with_calibration():
    w = world()
    request = build(w)
    first, second = run_monte_carlo(request), run_monte_carlo(request)
    assert first.model_dump() == second.model_dump()
    other = run_monte_carlo(request.model_copy(update={"seed": 8}))
    assert other.data.terminal_ltv_p95 != first.data.terminal_ltv_p95


# -- PR1 guards kept ---------------------------------------------------------------

def test_pr1_guards_still_hold():
    w = world()
    request = build(w)
    payload = request.model_dump(mode="json")
    no_calibration = json.loads(json.dumps(payload))
    no_calibration["simulation"]["calibration"] = None
    with pytest.raises(ValidationError, match="PR2"):
        MonteCarloRequest.model_validate(no_calibration)
    assumed_real = json.loads(json.dumps(payload))
    assumed_real["simulation"].update(source_mode="USER_ASSUMPTION", calibration=None)
    with pytest.raises(ValidationError, match="REAL_DATA requires OBSERVED_ORACLE_SERIES"):
        MonteCarloRequest.model_validate(assumed_real)
    edited = json.loads(json.dumps(payload))
    edited["simulation"]["annual_volatility"] = "0.5"
    with pytest.raises(ValidationError, match="match oracle calibration"):
        MonteCarloRequest.model_validate(edited)
    early = json.loads(json.dumps(payload))
    early["snapshot_as_of"] = "2026-10-09T23:00:00Z"
    with pytest.raises(ValidationError, match="future"):
        MonteCarloRequest.model_validate(early)
    stripped = json.loads(json.dumps(payload))
    stripped["evidence_ids"] = stripped["evidence_ids"][:1]
    with pytest.raises(ValidationError, match="evidence_ids"):
        MonteCarloRequest.model_validate(stripped)


def test_data_request_volatility_rules():
    with pytest.raises(ValidationError, match="annual_volatility"):
        MonteCarloDataRequest(strategy_id="s", position_id="p", horizon_days=7)
    with pytest.raises(ValidationError, match="calibrates volatility"):
        MonteCarloDataRequest(strategy_id="s", position_id="p", horizon_days=7,
                              volatility_source=SourceMode.OBSERVED_ORACLE_SERIES, annual_volatility="0.3")


# -- API and stores ----------------------------------------------------------------

def post(client, position, **changes):
    body = settings(position, **changes).model_dump(mode="json", exclude_none=True)
    return client.post("/api/v1/monte-carlo", json=body)


def test_api_snapshot_ok_and_unavailable():
    w = world()
    client = TestClient(create_app(snapshot=w.snapshot(), clock=lambda: NOW, replay=False))
    response = post(client, w.onchain)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "OK"
    assert body["data"]["provenance"]["calibration"] == "OBSERVED_ORACLE_SERIES"
    assert len(body["data"]["provenance"]["oracle_calibration"]["points"]) == 31
    assert isinstance(body["data"]["data"]["simulated_threshold_breach_frequency"], str)
    assert "NaN" not in response.text and "Infinity" not in response.text

    empty = TestClient(create_app(snapshot=w.snapshot(series=()), clock=lambda: NOW, replay=False))
    response = post(empty, w.onchain)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "UNAVAILABLE" and body["data"]["data"] is None
    assert body["missing_fields"] == ["oracle_series"]
    assert "NO_FIXTURE_SUBSTITUTED" in body["warnings"]

    assert post(client, w.onchain, position_id="pos_unknown_position").status_code == 404
    bad = settings(w.onchain).model_dump(mode="json", exclude_none=True) | {"annual_volatility": "0.3"}
    assert client.post("/api/v1/monte-carlo", json=bad).status_code == 422


@pytest.mark.parametrize("collateral", [Decimal("0"), Decimal("1000")])
def test_debt_free_position_is_not_applicable_without_calibration(collateral, monkeypatch):
    w = world(history_days=0)
    data = w.onchain.model_dump()
    data.update(collateral_amount=collateral, borrow_amount=Decimal("0"))
    position = PositionDTO.model_validate(data)
    snapshot = w.snapshot().model_copy(update={"positions": (position,)})
    client = TestClient(create_app(snapshot=snapshot, clock=lambda: NOW, replay=False))
    def no_simulation(*args, **kwargs):
        raise AssertionError("Debt-free positions must not run numerical simulation")
    monkeypatch.setattr("app.api.routes_analytics.run_monte_carlo", no_simulation)
    response = post(client, position)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NOT_APPLICABLE"
    assert body["data"]["data"] is None and body["missing_fields"] == []
    assert {"NO_DEBT", "NO_SIMULATION_RUN"} <= set(body["warnings"])


def test_stale_empty_position_does_not_claim_current_debt_free_status():
    w = world(history_days=0)
    position = w.onchain.model_copy(update={"collateral_amount": Decimal("0"), "borrow_amount": Decimal("0")})
    snapshot = w.snapshot().model_copy(update={"positions": (position,)})
    response = post(TestClient(create_app(snapshot=snapshot, clock=lambda: NOW + timedelta(minutes=6),
                                         replay=False)), position)
    assert response.status_code == 200 and response.json()["status"] == "UNAVAILABLE"
    assert "position.as_of" in response.json()["missing_fields"]


def seed_store(w, tmp_path, noise=True):
    """Persist the synthetic world as the Data pipeline would: Parquet rows carry no evidence ids."""
    db, root = tmp_path / "db.sqlite", tmp_path / "series"
    with SqliteStore(db) as store:
        store.save_markets([w.market])
        store.save_strategies([w.strategy])
        store.save_positions([w.simulated, w.onchain])
        store.save_evidence(w.evidence)
    rows = [Observation(entity_id=w.market.market_key, metric="ORACLE_PRICE", ts_utc=p.timestamp,
                        value_decimal=p.value, unit="TOKEN", source_id="ethereum_rpc",
                        retrieved_at=NOW - timedelta(hours=1), block_number=p.block_number,
                        pricing_method="MORPHO_ORACLE") for p in w.series[0].points]
    if noise:  # a midday refresh snapshot is not a sampling candidate
        rows.append(Observation(entity_id=w.market.market_key, metric="ORACLE_PRICE", ts_utc=NOW - timedelta(hours=2),
                                value_decimal=Decimal("0.5"), unit="TOKEN", source_id="ethereum_rpc",
                                retrieved_at=NOW - timedelta(hours=2), quality_flags=("SNAPSHOT",),
                                block_number=BLOCK - 600, pricing_method="MORPHO_ORACLE"))
    ParquetStore(root).write_observations(rows, DataMode.REAL)
    return db, root


def test_store_snapshot_links_point_proofs_and_calibrates(tmp_path):
    w = world()
    db, root = seed_store(w, tmp_path)
    with SqliteStore(db) as store:
        snapshot = snapshot_from_store(store, ParquetStore(root), DataMode.REAL)
    series, missing = select_oracle_series(snapshot.series, w.market)
    assert not missing and len(series.points) == 31
    assert all(len(p.evidence_ids) == 1 for p in series.points)
    calibration = calibrate_oracle_volatility(series, snapshot.markets[0], evidence=snapshot.evidence, as_of=NOW)
    assert abs(calibration.annual_volatility - expected_annual_volatility()) < Decimal("1e-15")

    client = TestClient(create_app(db, root, clock=lambda: NOW, replay=False))
    response = post(client, w.simulated)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "OK"
    assert "HYPOTHETICAL_POSITION_AMOUNTS" in body["data"]["warnings"]


def test_store_without_history_reports_unavailable(tmp_path):
    w = world(history_days=0)
    db, root = tmp_path / "db.sqlite", tmp_path / "series"
    with SqliteStore(db) as store:
        store.save_markets([w.market])
        store.save_strategies([w.strategy])
        store.save_positions([w.simulated, w.onchain])
        store.save_evidence(w.evidence)
    body = post(TestClient(create_app(db, root, clock=lambda: NOW, replay=False)), w.onchain).json()
    assert body["status"] == "UNAVAILABLE" and body["missing_fields"] == ["oracle_series"]


# -- retrospective collection (fake RPC, synthetic chain) ------------------------

class FakeChain:
    """SYNTHETIC chain: 12 s slots with one missed slot every 50 blocks."""
    base_number, base_time = 25_000_000, datetime(2026, 8, 1, tzinfo=timezone.utc)

    def __init__(self, now, archive_from=0):
        self.evidence = {}
        self.archive_from = archive_from
        self.head = self.base_number
        while self.time(self.head + 1) <= now:
            self.head += 1 if self.time(self.head + 1000) > now else 1000

    def time(self, n):
        d = n - self.base_number
        return self.base_time + timedelta(seconds=12 * d + 12 * (d // 50))

    def pin_block(self, identifier="latest"):
        n = self.head if identifier == "latest" else int(identifier)
        return PinnedBlock(number=n, hash=HASH, timestamp=self.time(n))

    def price(self, n):
        return LOW if (self.time(n).date().toordinal() % 2) else HIGH

    def call(self, address, abi, fn, args, pinned):
        if fn == "price" and pinned.number < self.archive_from:
            raise RpcTransportError("missing trie node (synthetic non-archive node)")
        value = {"idToMarketParams": (LOAN, PT, ORACLE, IRM, 915 * 10**15),
                 "decimals": 6 if address == LOAN else 18,
                 "price": raw(self.price(pinned.number)) if fn == "price" else None}[fn]
        method = f"eth_call {fn}({','.join(i['type'] for i in abi.get('inputs', []))})"
        ev = rpc_evidence(address, pinned.number, method, fetched_at=NOW - timedelta(minutes=5),
                          excerpt={"out0": str(value)} if fn != "idToMarketParams" else None)
        self.evidence[ev.evidence_id] = ev
        return RpcCallResult(value=value, raw_return="0x", evidence=ev,
                             source_ref=SourceRef("ethereum_rpc", ev.evidence_id, pinned.timestamp))


def test_block_at_or_before_is_exact():
    chain = FakeChain(NOW)
    latest = chain.pin_block()
    for target in (NOW - timedelta(days=30), NOW - timedelta(days=1, seconds=7), NOW - timedelta(seconds=30)):
        block = block_at_or_before(chain, target, latest)
        assert block.timestamp <= target < chain.time(block.number + 1)


def test_oracle_backfill_feeds_calibration(tmp_path):
    w = world(history_days=0)
    chain = FakeChain(NOW)
    db, root = tmp_path / "db.sqlite", tmp_path / "series"
    with SqliteStore(db) as store:
        store.save_markets([w.market])
        store.save_strategies([w.strategy])
        store.save_positions([w.simulated, w.onchain])
        store.save_evidence(w.evidence)
        report = run_oracle_backfill(chain, market_id=MARKET_ID, sqlite=store, parquet=ParquetStore(root),
                                     now=NOW, days=31, expected_collateral=PT, expected_loan=LOAN)
        assert report.ok_count == 31 and report.written_points == 31 and report.stopped_reason is None
        snapshot = snapshot_from_store(store, ParquetStore(root), DataMode.REAL)
    series, _ = select_oracle_series(snapshot.series, w.market)
    calibration = calibrate_oracle_volatility(series, snapshot.markets[0], evidence=snapshot.evidence, as_of=NOW)
    assert calibration.annual_volatility > 0


def test_oracle_backfill_without_archive_stops_and_writes_nothing_fake(tmp_path):
    chain = FakeChain(NOW, archive_from=FakeChain(NOW).head - 100)
    with SqliteStore(tmp_path / "db.sqlite") as store:
        report = run_oracle_backfill(chain, market_id=MARKET_ID, sqlite=store, parquet=ParquetStore(tmp_path / "ts"),
                                     now=NOW, days=31)
    assert report.ok_count == 0 and report.written_points == 0
    assert "archive" in report.stopped_reason and len(report.samples) == 3
    assert ParquetStore(tmp_path / "ts").read_observations(f"morpho:1:{MARKET_ID}", "ORACLE_PRICE", DataMode.REAL) == []


def test_oracle_backfill_keeps_accessible_recent_days_before_failure_limit(tmp_path):
    from app.ingestion.oracle_history import daily_targets
    chain = FakeChain(NOW)
    targets = daily_targets(NOW, 10)
    chain.archive_from = block_at_or_before(chain, targets[0], chain.pin_block()).number
    parquet = ParquetStore(tmp_path / "ts")
    with SqliteStore(tmp_path / "db.sqlite") as store:
        report = run_oracle_backfill(chain, market_id=MARKET_ID, sqlite=store, parquet=parquet,
                                     now=NOW, days=31)
    assert report.ok_count == report.written_points == 10
    assert len(report.samples) == 13 and report.stopped_reason is not None
    assert report.samples[0].target_time == targets[-1]
    rows = parquet.read_observations(f"morpho:1:{MARKET_ID}", "ORACLE_PRICE", DataMode.REAL)
    assert len(rows) == 10 and all(r.value_decimal is not None for r in rows)
    assert all(a.ts_utc < b.ts_utc for a, b in zip(rows, rows[1:]))
    assert rows[0].ts_utc <= targets[0] and rows[-1].ts_utc <= targets[-1]


def test_oracle_backfill_rejects_unexpected_collateral(tmp_path):
    with SqliteStore(tmp_path / "db.sqlite") as store, pytest.raises(ValueError, match="PT"):
        run_oracle_backfill(FakeChain(NOW), market_id=MARKET_ID, sqlite=store, parquet=ParquetStore(tmp_path),
                            now=NOW, expected_collateral=OTHER)


def test_market_price_dto_is_not_oracle():
    # Guard on the contract: a spot price object cannot carry the Morpho oracle method.
    with pytest.raises(ValidationError):
        MarketPriceDTO(chain_id=1, value=Decimal("1"), base_asset=PT_TOKEN, quote_unit=QuoteUnit.USD,
                       pricing_method=PricingMethod.MORPHO_ORACLE, observed_at=NOW, source_id="pendle_api",
                       evidence_ids=("ev_" + "a" * 24,), status=DataStatus.OK)
