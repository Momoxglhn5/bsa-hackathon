"""Read-only sized PT -> loan token quotes. Never broadcast/approve a transaction."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

import httpx

from app.adapters.pendle_api import DEFAULT_BASE_URL
from app.models.evidence import EvidenceDTO, make_evidence_id, normalize_address, to_decimal, to_raw_uint
from app.models.market import ExitPriceDTO

SIMULATION_RECEIVER = "0x1111111111111111111111111111111111111111"
QUOTE_TTL_SECONDS = 300  # local cache policy; not a provider execution guarantee


class QuoteUnavailable(ValueError):
    """The provider cannot supply a verifiable quote; never a zero price."""

    def __init__(self, message, *, evidence=None, raw_body=None):
        super().__init__(message)
        self.evidence = evidence
        self.raw_body = raw_body


def raw_amount(amount, decimals):
    amount = to_decimal(amount)
    if decimals is None or not 0 <= decimals <= 36 or amount <= 0:
        raise ValueError("Positive amount and verified token decimals required")
    with localcontext() as ctx:
        ctx.prec = 120
        raw = amount * Decimal(10) ** decimals
        if raw != raw.to_integral_value():
            raise ValueError("Amount exceeds token precision")
        return to_raw_uint(int(raw))


def select_route(payload, *, market, collateral, loan, amount):
    """Accept only routes proving the exact requested market, input and output."""
    raw = raw_amount(amount, collateral.decimals)
    if payload.get("inputs") != [{"token": collateral.address, "amount": str(raw)}]:
        raise ValueError("Quote input does not match request")
    choices = []
    for route in payload.get("routes", []):
        try:
            info = route["contractParamInfo"]
            params = dict(zip(info["contractCallParamsName"], info["contractCallParams"]))
            output = route["outputs"]
            if (info["method"] != "swapExactPtForToken"
                or normalize_address(params["market"]) != normalize_address(market)
                or normalize_address(params["receiver"]) != SIMULATION_RECEIVER
                or to_raw_uint(params["exactPtIn"]) != raw
                or len(output) != 1
                or normalize_address(output[0]["token"]) != loan.address
                or normalize_address(params["output"]["tokenOut"]) != loan.address):
                continue
            received = to_raw_uint(output[0]["amount"])
            minimum = to_raw_uint(params["output"]["minTokenOut"])
            if received <= 0 or minimum > received:
                continue
            choices.append((received, route))
        except (KeyError, TypeError, ValueError):
            continue
    if not choices:
        raise ValueError("No validated route for exact amount, market and loan token")
    return max(choices, key=lambda x: x[0])


class PendleQuoteClient:
    def __init__(self, *, transport=None, timeout_s=45):
        self.client = httpx.Client(base_url=DEFAULT_BASE_URL, transport=transport,
                                   timeout=timeout_s, headers={"accept": "application/json"})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def quote(self, *, market, collateral, loan, amount):
        if loan.decimals is None:
            raise ValueError("Verified loan token decimals required")
        amount = to_decimal(amount)
        body = {"receiver": SIMULATION_RECEIVER, "slippage": 0.01,
                "inputs": [{"token": collateral.address, "amount": str(raw_amount(amount, collateral.decimals))}],
                "outputs": [loan.address], "enableAggregator": True, "useLimitOrder": False}
        response = self.client.post("/v3/sdk/1/convert", json=body)
        now = datetime.now(timezone.utc)
        request = {"market": normalize_address(market), "body": json.dumps(body, sort_keys=True, separators=(",", ":"))}
        def source_evidence(status, error=None):
            return EvidenceDTO(chain_id=1, data_mode="REAL", source_kind="PENDLE_API",
                source_id="pendle_api", method="POST /v3/sdk/1/convert", request_params=request,
                fetched_at=now, payload_sha256=hashlib.sha256(response.content).hexdigest(),
                status=status, error_message=error,
                evidence_id=make_evidence_id("pendle_api", "sized_exit_quote", request, fetched_at=now))
        if not response.is_success:
            message = f"Pendle quote HTTP {response.status_code}: {response.text[:500]}"
            raise QuoteUnavailable(message, evidence=source_evidence("ERROR", message), raw_body=response.content)
        try:
            payload = json.loads(response.content, parse_float=Decimal)
            received, route = select_route(payload, market=market, collateral=collateral, loan=loan, amount=amount)
        except (ValueError, TypeError, AttributeError) as exc:
            message = f"Invalid provider quote: {exc}"
            raise QuoteUnavailable(message, evidence=source_evidence("ERROR", message), raw_body=response.content) from exc
        evidence = source_evidence("OK")
        with localcontext() as ctx:
            ctx.prec = 120
            output = Decimal(received) / Decimal(10) ** loan.decimals
            quote = ExitPriceDTO(chain_id=1, value=output / amount,
                base_asset=collateral, quote_unit="TOKEN", quote_asset=loan,
                pricing_method="EXECUTION_QUOTE", observed_at=now, source_id="pendle_api",
                evidence_ids=(evidence.evidence_id,), status="OK", input_amount=amount,
                output_amount=output, venue=str(route["data"]["aggregatorType"]),
                price_impact=route["data"].get("priceImpact"), valid_until=now + timedelta(seconds=QUOTE_TTL_SECONDS))
        details = {"min_output_amount_raw": str(to_raw_uint(dict(zip(
            route["contractParamInfo"]["contractCallParamsName"], route["contractParamInfo"]["contractCallParams"]))["output"]["minTokenOut"])),
            "gas_used_estimate": route["data"].get("gasUsed"),
            "reported_fee_usd": route["data"].get("fee", {}).get("usd"),
            "fee_treatment": "Provider-reported; additional closing fees unknown",
            "slippage_tolerance": "0.01", "cache_ttl_seconds": QUOTE_TTL_SECONDS,
            "warning": "Indicative quote only; simulation receiver; no approval or transaction sent"}
        return quote, evidence, details, response.content
