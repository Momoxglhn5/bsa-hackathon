"""Pendle PT exit price (spot swap quote), read-only, Ethereum mainnet first.

Third price concept of the brief (C4): PT_EXIT_EXECUTION_PRICE. It is NOT the PT
market price (/v1/prices/assets, USD) and NOT the Morpho oracle price. It is the
number of underlying tokens received for selling 1 whole PT at the current pool
state, as returned by GET /v1/sdk/{chainId}/markets/{market}/swapping-prices.

Self-contained on purpose: does not import or modify pendle_api.py.
Checked against the live API and its OpenAPI on 2026-10-10.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

SOURCE_ID = "pendle_sdk_swapping_prices"
METRIC = "PT_EXIT_EXECUTION_PRICE"
PRICING_METHOD = "EXECUTION_QUOTE_SPOT"  # per 1 PT, no size / slippage
_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


class PendleQuoteError(Exception):
    def __init__(self, code: str, message: str, retryable: bool, url: str,
                 http_status: int | None = None, rate_limit: dict | None = None):
        super().__init__(f"[{SOURCE_ID}] {code}: {message} ({url})")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.source_id = SOURCE_ID
        self.url = url
        self.http_status = http_status
        self.rate_limit = rate_limit or {}


@dataclass
class PtExitQuote:
    chain_id: int
    market_id: str  # pendle:<chain>:<market-lowercase>
    value: Decimal | None  # underlying tokens per 1 PT; None = not quotable
    quote_asset_id: str | None  # asset:<chain>:<underlying-lowercase>
    unit: str | None  # "<quote_asset_id> per PT"
    status: str  # OK | UNAVAILABLE
    missing_reason: str | None
    metric: str = METRIC
    pricing_method: str = PRICING_METHOD
    size_dependent: bool = False
    timestamp: str = ""  # ISO 8601 UTC, retrieval time (API says rates update every block)
    source_id: str = SOURCE_ID
    source_url: str = ""
    http_status: int | None = None
    raw: Any = None
    rate_limit: dict = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _rate_limit(headers: httpx.Headers) -> dict:
    return {k: v for k, v in headers.items() if k.startswith("x-ratelimit-") or k == "x-computing-unit"}


class PendleQuoteClient:
    def __init__(self, base_url: str | None = None, timeout: float = 20.0, max_attempts: int = 3,
                 backoff_base: float = 0.5, max_wait: float = 60.0, client: httpx.Client | None = None):
        self.base_url = (base_url or os.getenv("PENDLE_API_BASE")
                         or "https://api-v2.pendle.finance/core").rstrip("/")
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.max_wait = max_wait
        self._http = client or httpx.Client(timeout=timeout, headers={"accept": "application/json"})

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "PendleQuoteClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, url: str) -> tuple[httpx.Response, Any]:
        """GET with bounded retry on timeout / network / 429 / 5xx. Returns (response, Decimal-parsed JSON)."""
        last: PendleQuoteError | None = None
        for attempt in range(self.max_attempts):
            delay = self.backoff_base * 2 ** attempt
            try:
                resp = self._http.get(url)
            except httpx.TimeoutException as e:
                last = PendleQuoteError("TIMEOUT", str(e) or "request timed out", True, url)
            except httpx.TransportError as e:
                last = PendleQuoteError("NETWORK_ERROR", str(e), True, url)
            else:
                limits = _rate_limit(resp.headers)
                if resp.status_code == 429 or resp.status_code >= 500:
                    last = PendleQuoteError(f"HTTP_{resp.status_code}", resp.text[:300], True, url,
                                            resp.status_code, limits)
                    if resp.status_code == 429:
                        retry_after = resp.headers.get("retry-after", "")
                        reset = resp.headers.get("x-ratelimit-reset", "")  # epoch seconds
                        if retry_after.isdigit():
                            delay = float(retry_after)
                        elif reset.isdigit():
                            delay = max(float(reset) - time.time(), delay)
                        delay = min(delay, self.max_wait)
                else:
                    try:
                        # parse_float=Decimal: keep the API's digits, no float rounding.
                        return resp, json.loads(resp.text, parse_float=Decimal)
                    except ValueError:
                        raise PendleQuoteError("INVALID_JSON", resp.text[:300], False, url,
                                               resp.status_code, limits) from None
            if attempt + 1 < self.max_attempts:
                time.sleep(delay)
        assert last is not None
        raise last

    def get_pt_exit_quote(self, market_address: str, chain_id: int = 1) -> PtExitQuote:
        """Spot PT -> underlying rate for one Pendle market.

        404 (unknown or expired market) and a null rate (insufficient liquidity / matured)
        give status UNAVAILABLE with value None. Other 4xx raise PendleQuoteError.
        """
        if not isinstance(market_address, str) or not _ADDRESS.match(market_address):
            raise ValueError(f"invalid market_address: {market_address!r}")
        market = market_address.lower()
        url = f"{self.base_url}/v1/sdk/{chain_id}/markets/{market}/swapping-prices"
        resp, payload = self._get(url)
        base = dict(chain_id=chain_id, market_id=f"pendle:{chain_id}:{market}", timestamp=_now_iso(),
                    source_url=url, http_status=resp.status_code, raw=payload,
                    rate_limit=_rate_limit(resp.headers))

        if resp.status_code == 404:
            reason = payload.get("message") if isinstance(payload, dict) else None
            return PtExitQuote(value=None, quote_asset_id=None, unit=None, status="UNAVAILABLE",
                               missing_reason=reason or "HTTP 404", **base)
        if resp.status_code >= 400:
            raise PendleQuoteError(f"HTTP_{resp.status_code}", str(payload)[:300], False, url,
                                   resp.status_code, base["rate_limit"])

        underlying = payload.get("underlyingToken") if isinstance(payload, dict) else None
        quote_asset = (f"asset:{chain_id}:{underlying.lower()}"
                       if isinstance(underlying, str) and _ADDRESS.match(underlying) else None)
        rate = payload.get("ptToUnderlyingTokenRate") if isinstance(payload, dict) else None
        if isinstance(rate, int) and not isinstance(rate, bool):
            rate = Decimal(rate)

        reason = None
        if quote_asset is None:
            reason = "underlyingToken missing or not an address"
        elif rate is None:
            reason = "ptToUnderlyingTokenRate is null (swap not possible: liquidity or maturity)"
        elif not isinstance(rate, Decimal) or not rate.is_finite():
            reason = f"ptToUnderlyingTokenRate has unexpected type/value: {rate!r}"
        if reason:
            return PtExitQuote(value=None, quote_asset_id=quote_asset,
                               unit=f"{quote_asset} per PT" if quote_asset else None,
                               status="UNAVAILABLE", missing_reason=reason, **base)
        return PtExitQuote(value=rate, quote_asset_id=quote_asset, unit=f"{quote_asset} per PT",
                           status="OK", missing_reason=None, **base)


if __name__ == "__main__":
    with PendleQuoteClient() as client:
        # Pick live active Ethereum markets (discovery only; selection is not this module's job).
        _, listing = client._get(f"{client.base_url}/v2/markets/all?chainId=1&isActive=true&skip=0&limit=3")
        for m in listing["results"]:
            q = client.get_pt_exit_quote(m["address"])
            print(f"{q.market_id} [{q.status}] {q.metric}={q.value} {q.unit}\n"
                  f"  method={q.pricing_method} size_dependent={q.size_dependent} at={q.timestamp}")
            assert q.status in ("OK", "UNAVAILABLE") and (q.value is None) == (q.status != "OK")

        expired = client.get_pt_exit_quote("0x00b321d89a8c36b3929f20b7955080baed706d1b")  # matured 2024-10-31
        print(f"expired market [{expired.status}] value={expired.value} reason={expired.missing_reason!r}")
        assert expired.status == "UNAVAILABLE" and expired.value is None

        unknown = client.get_pt_exit_quote("0x0000000000000000000000000000000000000bad")
        assert unknown.status == "UNAVAILABLE" and unknown.http_status == 404
        print(f"unknown market [{unknown.status}] reason={unknown.missing_reason!r}")
        print("rate limit:", {k: unknown.rate_limit.get(k) for k in ("x-ratelimit-remaining", "x-computing-unit")})
