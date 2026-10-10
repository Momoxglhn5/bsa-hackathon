"""Pendle API adapter — discovery and metrics only.

Scope (data contract v1.0):
  * raw payloads with provenance (source_id, retrieved_at, sha256 of body,
    X-RateLimit-* headers seen);
  * no normalisation into DTOs, no business/risk calculations;
  * values kept exactly as returned: absent keys stay absent, null stays None,
    JSON floats parsed as Decimal. Liquidity/TVL are never derived or defaulted;
  * APY fields are already ratios in the API (verified live: impliedApy 0.1819 == 18.19 %)
    and are passed through unchanged;
  * per-page / per-market / per-chunk failures are kept in `errors` next to the
    data that did succeed (partial results), never swallowed.

Endpoints (public Core API only; the internal BFF API is refused):
  * GET /v2/markets/all                                   — verified live (skip/limit, total)
  * GET /v3/{chainId}/markets/{address}/historical-data   — verified live
    (time_frame, timestamp_start, timestamp_end → {total, timestamp_start,
    timestamp_end, results[{timestamp, maxApy, baseApy, underlyingApy, impliedApy, tvl}]})
  * GET /v1/prices/assets (optional)                      — verified live
    ({prices: {"<chainId>-<address>": number}, total, skip, limit}); the payload does not
    state the currency — confirm before mapping to a USD MarketPriceDTO.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Iterator, Literal

import httpx

from app.models.evidence import (
    ETHEREUM_MAINNET_CHAIN_ID,
    DataMode,
    DataStatus,
    EvidenceDTO,
    SourceKind,
    make_evidence_id,
    normalize_address,
)

logger = logging.getLogger(__name__)

SOURCE_ID = "pendle_api"
CHAIN_ID = ETHEREUM_MAINNET_CHAIN_ID

BASE_URL_ENV = "PENDLE_API_BASE_URL"
TIMEOUT_ENV = "PENDLE_API_TIMEOUT_S"
DEFAULT_BASE_URL = "https://api-v2.pendle.finance/core"
DEFAULT_TIMEOUT_S = 20.0

MARKETS_PATH = "/v2/markets/all"
HISTORICAL_PATH = "/v3/{chain_id}/markets/{address}/historical-data"
PRICES_PATH = "/v1/prices/assets"

_ALLOWED_PATHS = (
    re.compile(r"^/v2/markets/all$"),
    re.compile(r"^/v3/1/markets/0x[0-9a-f]{40}/historical-data$"),
    re.compile(r"^/v1/prices/assets$"),
)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
DEFAULT_MAX_ATTEMPTS = 4
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 8.0
RATE_LIMIT_WAIT_CAP_S = 30.0

DEFAULT_PAGE_LIMIT = 100
DEFAULT_MAX_PAGES = 30
MAX_IDS_PER_PRICE_CALL = 20
_BODY_EXCERPT_CHARS = 500

TimeFrame = Literal["hour", "day", "week"]
AssetType = Literal["PENDLE_LP", "SY", "PT", "YT"]
_PENDLE_ID_RE = re.compile(r"^(\d+)-(0x[0-9a-fA-F]{40})$")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class PendleAPIError(Exception):
    def __init__(
        self,
        message: str,
        *,
        url: str | None = None,
        status_code: int | None = None,
        body_excerpt: str | None = None,
        attempts: int | None = None,
        rate_limit: dict[str, str] | None = None,
    ) -> None:
        details = [message]
        if url:
            details.append(f"url={url}")
        if status_code is not None:
            details.append(f"status={status_code}")
        if attempts is not None:
            details.append(f"attempts={attempts}")
        if rate_limit:
            details.append(f"rate_limit={rate_limit}")
        if body_excerpt:
            details.append(f"body={body_excerpt!r}")
        super().__init__(" | ".join(details))
        self.url = url
        self.status_code = status_code
        self.body_excerpt = body_excerpt
        self.attempts = attempts
        self.rate_limit = rate_limit or {}


class PendleHTTPError(PendleAPIError):
    """Non-2xx response (after retries for retryable statuses)."""


class PendleRateLimitError(PendleHTTPError):
    """429 on every attempt."""


class PendleTimeoutError(PendleAPIError):
    """Request timed out on every attempt."""


class PendleTransportError(PendleAPIError):
    """Connection-level failure on every attempt."""


class PendleResponseShapeError(PendleAPIError):
    """Response body does not match the expected envelope."""


class PendleEndpointNotAllowed(ValueError):
    """Path outside the public Core endpoints this adapter may call (e.g. BFF)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_pendle_id(value: Any) -> tuple[int, str]:
    """Split a Pendle id "<chainId>-<address>" into (chain_id, normalised address)."""
    if not isinstance(value, str):
        raise ValueError(f"Pendle id must be a string, got {type(value).__name__}")
    match = _PENDLE_ID_RE.match(value.strip())
    if not match:
        raise ValueError(f"invalid Pendle id: {value!r}")
    return int(match.group(1)), normalize_address(match.group(2))


def to_pendle_id(address: str, chain_id: int = CHAIN_ID) -> str:
    return f"{chain_id}-{normalize_address(address)}"


def _excerpt(body: bytes) -> str:
    return body[:_BODY_EXCERPT_CHARS].decode("utf-8", errors="replace")


def _iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_rate_limit(headers: httpx.Headers) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items() if k.lower().startswith("x-ratelimit-")}


def _seconds_until(value: str | None) -> float | None:
    """Interpret a reset/retry value: delta seconds, epoch seconds or epoch ms."""
    if value is None:
        return None
    try:
        v = float(value)
    except ValueError:
        return None
    now = time.time()
    if v > 1e12:
        v = v / 1000 - now
    elif v > 1e9:
        v = v - now
    return max(v, 0.0)


def resolve_base_url() -> str:
    base = os.environ.get(BASE_URL_ENV, "").strip().rstrip("/") or DEFAULT_BASE_URL
    if "/bff" in base.lower():
        raise PendleEndpointNotAllowed(f"{BASE_URL_ENV} points to the internal BFF API: {base}")
    return base


def resolve_timeout() -> float:
    raw = os.environ.get(TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{TIMEOUT_ENV} must be > 0")
    return value


# ---------------------------------------------------------------------------
# Raw containers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RawResponse:
    source_id: str
    operation: str
    url: str
    request_params: dict[str, str]
    status_code: int
    requested_at: datetime
    retrieved_at: datetime
    payload: Any
    payload_sha256: str
    attempts: int
    rate_limit: dict[str, str]
    raw_body: bytes = field(repr=False)

    @property
    def evidence_id(self) -> str:
        return make_evidence_id(
            self.source_id, self.operation, self.request_params, fetched_at=self.retrieved_at
        )

    def to_evidence(self) -> EvidenceDTO:
        return EvidenceDTO(
            chain_id=CHAIN_ID,
            data_mode=DataMode.REAL,
            evidence_id=self.evidence_id,
            source_kind=SourceKind.PENDLE_API,
            source_id=self.source_id,
            method=f"GET {self.operation}",
            request_params=self.request_params,
            fetched_at=self.retrieved_at,
            payload_sha256=self.payload_sha256,
            status=DataStatus.OK,
        )


@dataclass(frozen=True)
class ExcludedItem:
    reason: str
    item: Any


@dataclass(frozen=True)
class MarketsPage:
    response: RawResponse
    items: tuple[dict[str, Any], ...]          # chainId == 1 only
    excluded: tuple[ExcludedItem, ...]         # other chain / missing chainId / not an object
    total: int


@dataclass(frozen=True)
class MarketListing:
    pages: tuple[MarketsPage, ...]
    errors: tuple[PendleAPIError, ...]
    complete: bool
    stop_reason: str

    def iter_items(self) -> Iterator[tuple[dict[str, Any], RawResponse]]:
        for page in self.pages:
            for item in page.items:
                yield item, page.response

    @property
    def item_count(self) -> int:
        return sum(len(p.items) for p in self.pages)

    @property
    def excluded(self) -> tuple[ExcludedItem, ...]:
        return tuple(e for p in self.pages for e in p.excluded)


@dataclass(frozen=True)
class HistoricalBatch:
    results: dict[str, RawResponse]
    errors: dict[str, Exception]

    @property
    def complete(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class PricesResult:
    responses: tuple[RawResponse, ...]
    errors: tuple[tuple[tuple[str, ...], PendleAPIError], ...]   # (requested ids, error)
    prices: dict[str, Decimal | None]   # only ids present in a response; null kept as None
    requested_ids: tuple[str, ...]

    @property
    def missing_ids(self) -> tuple[str, ...]:
        return tuple(i for i in self.requested_ids if i not in self.prices)

    @property
    def complete(self) -> bool:
        return not self.errors and not self.missing_ids


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class PendleApiClient:
    """Thin HTTP client for the public Pendle Core API. Ethereum mainnet only."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_s: float | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.base_url = (base_url or resolve_base_url()).rstrip("/")
        if "/bff" in self.base_url.lower():
            raise PendleEndpointNotAllowed(f"internal BFF API not allowed: {self.base_url}")
        self.timeout_s = timeout_s if timeout_s is not None else resolve_timeout()
        self.max_attempts = max_attempts
        self._sleep = sleep
        self.last_rate_limit: dict[str, str] = {}
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout_s),
            headers={"accept": "application/json", "user-agent": "domino-data-pipeline/1.0"},
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PendleApiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transport ----------------------------------------------------------

    def _wait_if_exhausted(self) -> None:
        """Pause when the last response reported zero remaining requests."""
        remaining = self.last_rate_limit.get("x-ratelimit-remaining")
        if remaining is None:
            return
        try:
            if float(remaining) > 0:
                return
        except ValueError:
            return
        delay = _seconds_until(self.last_rate_limit.get("x-ratelimit-reset"))
        if delay:
            delay = min(delay, RATE_LIMIT_WAIT_CAP_S)
            logger.warning("pendle_api rate limit exhausted, waiting %.2fs", delay)
            self._sleep(delay)

    def _retry_delay(self, attempt: int, resp: httpx.Response | None) -> float:
        if resp is not None and resp.status_code == 429:
            hinted = _seconds_until(resp.headers.get("retry-after"))
            if hinted is None:
                hinted = _seconds_until(resp.headers.get("x-ratelimit-reset"))
            if hinted is not None:
                return min(hinted, RATE_LIMIT_WAIT_CAP_S)
        delay = min(BACKOFF_BASE_S * 2 ** (attempt - 1), BACKOFF_CAP_S)
        return delay + random.uniform(0, BACKOFF_BASE_S)

    def _get(self, path: str, operation: str, params: dict[str, str]) -> RawResponse:
        if not any(p.match(path) for p in _ALLOWED_PATHS):
            raise PendleEndpointNotAllowed(f"path not allowed: {path}")
        url = f"{self.base_url}{path}"
        for attempt in range(1, self.max_attempts + 1):
            self._wait_if_exhausted()
            resp: httpx.Response | None = None
            requested_at = datetime.now(timezone.utc)
            try:
                resp = self._client.request("GET", path, params=params)
            except httpx.TimeoutException as exc:
                failure: PendleAPIError = PendleTimeoutError(
                    f"timeout after {self.timeout_s}s: {exc}", url=url, attempts=attempt
                )
            except httpx.TransportError as exc:
                failure = PendleTransportError(f"transport error: {exc}", url=url, attempts=attempt)
            else:
                rate_limit = _extract_rate_limit(resp.headers)
                if rate_limit:
                    self.last_rate_limit = rate_limit
                body = resp.content
                if 200 <= resp.status_code < 300:
                    return self._build_raw(
                        resp, body, url, operation, params, attempt, rate_limit, requested_at
                    )
                error_cls = PendleRateLimitError if resp.status_code == 429 else PendleHTTPError
                failure = error_cls(
                    "HTTP error", url=url, status_code=resp.status_code,
                    body_excerpt=_excerpt(body), attempts=attempt, rate_limit=rate_limit,
                )
                if resp.status_code not in RETRYABLE_STATUS:
                    raise failure

            if attempt == self.max_attempts:
                raise failure
            delay = self._retry_delay(attempt, resp)
            logger.warning("pendle_api %s failed (attempt %d/%d), retrying in %.2fs: %s",
                           operation, attempt, self.max_attempts, delay, failure)
            self._sleep(delay)
            self.last_rate_limit = {}  # already waited; avoid a second proactive wait
        raise AssertionError("unreachable")

    def _build_raw(
        self,
        resp: httpx.Response,
        body: bytes,
        url: str,
        operation: str,
        params: dict[str, str],
        attempts: int,
        rate_limit: dict[str, str],
        requested_at: datetime,
    ) -> RawResponse:
        retrieved_at = datetime.now(timezone.utc)
        try:
            payload = json.loads(body, parse_float=Decimal)
        except (ValueError, UnicodeDecodeError) as exc:
            raise PendleResponseShapeError(
                f"invalid JSON: {exc}", url=url, status_code=resp.status_code,
                body_excerpt=_excerpt(body), attempts=attempts,
            ) from exc
        return RawResponse(
            source_id=SOURCE_ID,
            operation=operation,
            url=url,
            request_params=dict(params),
            status_code=resp.status_code,
            requested_at=requested_at,
            retrieved_at=retrieved_at,
            payload=payload,
            payload_sha256=hashlib.sha256(body).hexdigest(),
            attempts=attempts,
            rate_limit=rate_limit,
            raw_body=body,
        )

    # -- markets --------------------------------------------------------------

    def list_markets_page(
        self, skip: int = 0, limit: int = DEFAULT_PAGE_LIMIT, is_active: bool | None = None
    ) -> MarketsPage:
        """One page of /v2/markets/all, filtered to chainId == 1 (others kept in `excluded`)."""
        if skip < 0 or limit < 1:
            raise ValueError("require skip >= 0 and limit >= 1")
        params = {"skip": str(skip), "limit": str(limit), "chainId": str(CHAIN_ID)}
        if is_active is not None:
            params["isActive"] = "true" if is_active else "false"
        raw = self._get(MARKETS_PATH, "rest:/v2/markets/all", params)

        payload = raw.payload
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise PendleResponseShapeError(
                "expected object with list `results`", url=raw.url, body_excerpt=_excerpt(raw.raw_body)
            )
        total = payload.get("total")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            raise PendleResponseShapeError(f"invalid `total`: {total!r}", url=raw.url)

        kept: list[dict[str, Any]] = []
        excluded: list[ExcludedItem] = []
        for item in payload["results"]:
            if not isinstance(item, dict):
                excluded.append(ExcludedItem("not an object", item))
            elif "chainId" not in item:
                excluded.append(ExcludedItem("missing chainId", item))
            elif item["chainId"] != CHAIN_ID:
                excluded.append(ExcludedItem(f"chainId={item['chainId']!r}", item))
            else:
                kept.append(item)
        return MarketsPage(raw, tuple(kept), tuple(excluded), total)

    def list_markets(
        self,
        limit: int = DEFAULT_PAGE_LIMIT,
        max_pages: int = DEFAULT_MAX_PAGES,
        is_active: bool | None = None,
    ) -> MarketListing:
        """All mainnet markets via skip/limit. A failed page stops paging; earlier pages are kept."""
        if max_pages < 1:
            raise ValueError("max_pages must be >= 1")
        pages: list[MarketsPage] = []
        skip = 0
        first_total: int | None = None
        for _ in range(max_pages):
            try:
                page = self.list_markets_page(skip=skip, limit=limit, is_active=is_active)
            except PendleAPIError as exc:
                logger.error("pendle_api page skip=%d failed: %s", skip, exc)
                return MarketListing(tuple(pages), (exc,), False, f"page at skip={skip} failed")
            pages.append(page)
            if first_total is None:
                first_total = page.total
            elif page.total != first_total:
                logger.warning("pendle_api total changed during paging: %d -> %d", first_total, page.total)
            returned = len(page.items) + len(page.excluded)
            skip += returned
            if returned == 0:
                complete = skip >= page.total
                reason = "all pages read" if complete else f"empty page before total={page.total}"
                return MarketListing(tuple(pages), (), complete, reason)
            if skip >= page.total:
                return MarketListing(tuple(pages), (), True, "all pages read")
        logger.warning("pendle_api list_markets stopped at max_pages=%d: listing incomplete", max_pages)
        return MarketListing(tuple(pages), (), False, f"max_pages={max_pages} reached")

    # -- historical data ----------------------------------------------------

    def fetch_market_history(
        self,
        market_address: str,
        time_frame: TimeFrame = "day",
        start: datetime | None = None,
        end: datetime | None = None,
        fields: str | None = None,
    ) -> RawResponse:
        """Time series for one mainnet market. Points are returned untouched."""
        if time_frame not in ("hour", "day", "week"):
            raise ValueError(f"invalid time_frame: {time_frame!r}")
        if start is not None and end is not None and start >= end:
            raise ValueError("start must be before end")
        address = normalize_address(market_address)
        params: dict[str, str] = {"time_frame": time_frame}
        if start is not None:
            params["timestamp_start"] = _iso_utc(start)
        if end is not None:
            params["timestamp_end"] = _iso_utc(end)
        if fields is not None:
            params["fields"] = fields
        path = HISTORICAL_PATH.format(chain_id=CHAIN_ID, address=address)
        raw = self._get(path, "rest:/v3/markets/historical-data", params)

        payload = raw.payload
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise PendleResponseShapeError(
                "expected object with list `results`", url=raw.url, body_excerpt=_excerpt(raw.raw_body)
            )
        return raw

    def fetch_market_history_batch(
        self,
        market_addresses: Iterable[str],
        time_frame: TimeFrame = "day",
        start: datetime | None = None,
        end: datetime | None = None,
        fields: str | None = None,
    ) -> HistoricalBatch:
        """History for several markets; each failure is recorded, the others are kept."""
        results: dict[str, RawResponse] = {}
        errors: dict[str, Exception] = {}
        for addr in market_addresses:
            key = addr.strip().lower() if isinstance(addr, str) else repr(addr)
            try:
                results[normalize_address(addr)] = self.fetch_market_history(
                    addr, time_frame=time_frame, start=start, end=end, fields=fields
                )
            except (PendleAPIError, ValueError) as exc:
                logger.error("pendle_api history %s failed: %s", key, exc)
                errors[key] = exc
        return HistoricalBatch(results, errors)

    # -- prices (optional) ---------------------------------------------------

    def fetch_asset_prices(
        self,
        addresses: Iterable[str],
        asset_type: AssetType | None = None,
    ) -> PricesResult:
        """Prices for mainnet assets, chunked by 20 ids. Missing ids stay missing."""
        ids = tuple(dict.fromkeys(to_pendle_id(a) for a in addresses))
        if not ids:
            raise ValueError("at least one address is required")
        if asset_type is not None and asset_type not in ("PENDLE_LP", "SY", "PT", "YT"):
            raise ValueError(f"invalid asset_type: {asset_type!r}")

        responses: list[RawResponse] = []
        errors: list[tuple[tuple[str, ...], PendleAPIError]] = []
        prices: dict[str, Decimal | None] = {}
        for i in range(0, len(ids), MAX_IDS_PER_PRICE_CALL):
            chunk = ids[i:i + MAX_IDS_PER_PRICE_CALL]
            params = {"ids": ",".join(chunk), "chainId": str(CHAIN_ID)}
            if asset_type is not None:
                params["type"] = asset_type
            try:
                raw = self._get(PRICES_PATH, "rest:/v1/prices/assets", params)
                payload = raw.payload
                if not isinstance(payload, dict) or not isinstance(payload.get("prices"), dict):
                    raise PendleResponseShapeError(
                        "expected object with dict `prices`", url=raw.url,
                        body_excerpt=_excerpt(raw.raw_body),
                    )
            except PendleAPIError as exc:
                logger.error("pendle_api prices chunk %d failed: %s", i // MAX_IDS_PER_PRICE_CALL, exc)
                errors.append((chunk, exc))
                continue
            responses.append(raw)
            for key, value in payload["prices"].items():
                try:
                    chain_id, address = parse_pendle_id(key)
                except ValueError:
                    logger.warning("pendle_api prices: unparseable id %r ignored", key)
                    continue
                if chain_id != CHAIN_ID:
                    continue
                if isinstance(value, bool) or (
                    value is not None and not isinstance(value, (Decimal, int))
                ):
                    logger.warning("pendle_api prices: non-numeric value for %s: %r", key, value)
                    continue
                prices[f"{chain_id}-{address}"] = Decimal(value) if isinstance(value, int) else value
        return PricesResult(tuple(responses), tuple(errors), prices, ids)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def list_markets(
    limit: int = DEFAULT_PAGE_LIMIT, max_pages: int = DEFAULT_MAX_PAGES, is_active: bool | None = None
) -> MarketListing:
    with PendleApiClient() as client:
        return client.list_markets(limit=limit, max_pages=max_pages, is_active=is_active)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _show(obj: Any, *path: str) -> str:
    for key in path:
        if not isinstance(obj, dict) or key not in obj:
            return "NOT PRESENT"
        obj = obj[key]
    return str(obj)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pendle API adapter smoke test (chainId=1)")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--all-markets", action="store_true", help="include expired/inactive markets")
    parser.add_argument("--history-days", type=int, default=3)
    parser.add_argument("--prices", action="store_true", help="also fetch PT / underlying prices")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    with PendleApiClient() as client:
        print(f"source_id={SOURCE_ID} base_url={client.base_url} chainId={CHAIN_ID}")
        try:
            page = client.list_markets_page(limit=args.limit, is_active=None if args.all_markets else True)
        except PendleAPIError as exc:
            print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        r = page.response
        print(f"retrieved_at={r.retrieved_at.isoformat()} total={page.total} "
              f"kept={len(page.items)} excluded={len(page.excluded)} rate_limit={r.rate_limit or 'none'}")
        for m in page.items:
            print(f"- {_show(m, 'name')} market={_show(m, 'address')} expiry={_show(m, 'expiry')}\n"
                  f"  pt={_show(m, 'pt')} sy={_show(m, 'sy')} yt={_show(m, 'yt')}\n"
                  f"  impliedApy={_show(m, 'details', 'impliedApy')} "
                  f"liquidity={_show(m, 'details', 'liquidity')} totalTvl={_show(m, 'details', 'totalTvl')}")
        if not page.items:
            return 0

        first = page.items[0]
        end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        try:
            hist = client.fetch_market_history(
                first["address"], "day", end - timedelta(days=args.history_days), end
            )
            points = hist.payload["results"]
            print(f"history {first['address']}: points={len(points)} "
                  f"retrieved_at={hist.retrieved_at.isoformat()}")
            for p in points[-3:]:
                print(f"  {_show(p, 'timestamp')} impliedApy={_show(p, 'impliedApy')} tvl={_show(p, 'tvl')}")
        except PendleAPIError as exc:
            print(f"history ERROR {type(exc).__name__}: {exc}", file=sys.stderr)

        if args.prices:
            ids = [parse_pendle_id(first[k])[1] for k in ("pt", "underlyingAsset") if k in first]
            res = client.fetch_asset_prices(ids)
            print(f"prices={ {k: str(v) for k, v in res.prices.items()} } "
                  f"missing={list(res.missing_ids)} errors={len(res.errors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())