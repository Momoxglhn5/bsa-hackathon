"""Morpho API adapter — discovery and metrics only.

Scope (data contract v1.0):
  * fetch raw payloads from the Morpho API with provenance (source_id, retrieved_at,
    sha256 of the response body);
  * no normalisation into DTOs, no business/risk calculations;
  * missing values stay exactly as returned (absent key / null) — never replaced by 0;
  * API errors are raised, never swallowed.

Endpoint status:
  * REST  GET /v1/blue/markets — VERIFIED against a captured live response
    (envelope {data: [...], cursor}).
  * Pagination param `cursor` — documented for the Morpho REST API (opaque cursor,
    null on last page); loop guards below raise if the server ignores it.
  * GraphQL /graphql `marketById { state { ... } }` — field names taken from the
    Morpho docs, NOT yet validated against the live schema. Unknown fields surface
    as MorphoGraphQLError. Run `--schema Market` / `--schema MarketState` to check.
  * Market / APY history — GraphQL `marketById.historicalState`, verified by
    introspection on 2026-10-10 (see MARKET_HISTORY_QUERY).
  * Oracle state — no verified endpoint yet: raises MorphoEndpointUnavailable.
    The authoritative oracle price is read on-chain via RPC (IOracle.price()).
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
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterator

import httpx

from app.models.evidence import (
    ETHEREUM_MAINNET_CHAIN_ID,
    DataMode,
    DataStatus,
    EvidenceDTO,
    SourceKind,
    make_evidence_id,
    normalize_address,
    normalize_bytes32,
)

logger = logging.getLogger(__name__)

SOURCE_ID = "morpho_api"
CHAIN_ID = ETHEREUM_MAINNET_CHAIN_ID

BASE_URL_ENV = "MORPHO_API_BASE_URL"
TIMEOUT_ENV = "MORPHO_API_TIMEOUT_S"
DEFAULT_BASE_URL = "https://api.morpho.org"
DEFAULT_TIMEOUT_S = 15.0

MARKETS_PATH = "/v1/blue/markets"
GRAPHQL_PATH = "/graphql"

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
DEFAULT_MAX_ATTEMPTS = 4
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 8.0

DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 1000
DEFAULT_MAX_PAGES = 50
_BODY_EXCERPT_CHARS = 500
_GRAPHQL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Docs-derived, not yet validated against the live schema.
MARKET_DETAILS_QUERY = """
query {
  marketById(marketId: "%(market_id)s", chainId: %(chain_id)d) {
    state {
      supplyAssets
      borrowAssets
      liquidityAssets
      supplyApy
      borrowApy
    }
  }
}
"""

# No verified query yet. Set only after checking the live schema
# (placeholders: market_id, chain_id, start_ts, end_ts, interval).
ORACLE_STATE_QUERY: str | None = None

# Verified by introspection on 2026-10-10: Market.historicalState -> MarketHistory;
# each field takes `options: TimeseriesOptions {startTimestamp: Int, endTimestamp: Int,
# interval: TimeseriesInterval}` and returns [{x: Float (unix s), y}] with
# y BigInt (assets) or Float (ratios).
_HISTORY_OPTIONS = "(options: {startTimestamp: %(start_ts)d, endTimestamp: %(end_ts)d, interval: %(interval)s})"
MARKET_HISTORY_QUERY: str | None = (
    'query { marketById(marketId: "%(market_id)s", chainId: %(chain_id)d) { historicalState {'
    f" supplyAssets{_HISTORY_OPTIONS} {{ x y }}"
    f" borrowAssets{_HISTORY_OPTIONS} {{ x y }}"
    f" utilization{_HISTORY_OPTIONS} {{ x y }}"
    " } } }"
)
APY_HISTORY_QUERY: str | None = (
    'query { marketById(marketId: "%(market_id)s", chainId: %(chain_id)d) { historicalState {'
    f" supplyApy{_HISTORY_OPTIONS} {{ x y }}"
    f" borrowApy{_HISTORY_OPTIONS} {{ x y }}"
    " } } }"
)
HISTORY_INTERVALS = frozenset({"HOUR", "DAY", "WEEK", "MONTH", "QUARTER", "YEAR"})

SCHEMA_TYPE_QUERY = """
query {
  __type(name: "%(type_name)s") {
    name
    fields { name type { name kind ofType { name kind } } }
  }
}
"""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class MorphoAPIError(Exception):
    """Base error. Carries enough context to debug without re-running."""

    def __init__(
        self,
        message: str,
        *,
        url: str | None = None,
        status_code: int | None = None,
        body_excerpt: str | None = None,
        attempts: int | None = None,
    ) -> None:
        details = [message]
        if url:
            details.append(f"url={url}")
        if status_code is not None:
            details.append(f"status={status_code}")
        if attempts is not None:
            details.append(f"attempts={attempts}")
        if body_excerpt:
            details.append(f"body={body_excerpt!r}")
        super().__init__(" | ".join(details))
        self.url = url
        self.status_code = status_code
        self.body_excerpt = body_excerpt
        self.attempts = attempts


class MorphoHTTPError(MorphoAPIError):
    """Non-2xx response (after retries for retryable statuses)."""


class MorphoTimeoutError(MorphoAPIError):
    """Request timed out on every attempt."""


class MorphoTransportError(MorphoAPIError):
    """Connection-level failure on every attempt."""


class MorphoResponseShapeError(MorphoAPIError):
    """Response body does not match the expected envelope."""


class MorphoGraphQLError(MorphoAPIError):
    """GraphQL `errors` array present. Partial data is kept on the exception."""

    def __init__(self, message: str, *, errors: Any, partial_data: Any, response: "RawResponse") -> None:
        super().__init__(message, url=response.url, status_code=response.status_code)
        self.errors = errors
        self.partial_data = partial_data
        self.response = response


class MorphoPaginationError(MorphoAPIError):
    """Server ignored or looped the pagination cursor."""


class MorphoEndpointUnavailable(MorphoAPIError):
    """No verified endpoint/query exists for this data yet."""


# ---------------------------------------------------------------------------
# Raw response containers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RawResponse:
    """One successful HTTP exchange, unmodified apart from JSON parsing.

    JSON floats are parsed as Decimal; nothing else is converted or defaulted.
    """

    source_id: str
    operation: str
    http_method: str
    url: str
    request_params: dict[str, str]
    request_body: dict[str, Any] | None
    status_code: int
    requested_at: datetime
    retrieved_at: datetime
    payload: Any
    payload_sha256: str
    attempts: int
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
            source_kind=SourceKind.MORPHO_API,
            source_id=self.source_id,
            method=f"{self.http_method} {self.operation}",
            request_params=self.request_params,
            fetched_at=self.retrieved_at,
            payload_sha256=self.payload_sha256,
            status=DataStatus.OK,
        )


@dataclass(frozen=True)
class MarketsPage:
    response: RawResponse
    items: tuple[dict[str, Any], ...]
    next_cursor: str | None


@dataclass(frozen=True)
class MarketListing:
    """Pages fetched so far. `complete` is False on a page error or max_pages."""

    pages: tuple[MarketsPage, ...]
    errors: tuple[MorphoAPIError, ...]
    complete: bool
    stop_reason: str

    def iter_items(self) -> Iterator[tuple[dict[str, Any], RawResponse]]:
        """Yield each raw market item with the response it came from (provenance)."""
        for page in self.pages:
            for item in page.items:
                yield item, page.response

    @property
    def item_count(self) -> int:
        return sum(len(p.items) for p in self.pages)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def _excerpt(body: bytes) -> str:
    return body[:_BODY_EXCERPT_CHARS].decode("utf-8", errors="replace")


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def resolve_base_url() -> str:
    return os.environ.get(BASE_URL_ENV, "").strip().rstrip("/") or DEFAULT_BASE_URL


def resolve_timeout() -> float:
    raw = os.environ.get(TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{TIMEOUT_ENV} must be > 0")
    return value


class MorphoApiClient:
    """Thin HTTP client for the Morpho API. Ethereum mainnet only (chain_id=1)."""

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
        self.timeout_s = timeout_s if timeout_s is not None else resolve_timeout()
        self.max_attempts = max_attempts
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout_s),
            headers={"accept": "application/json", "user-agent": "domino-data-pipeline/1.0"},
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "MorphoApiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transport ----------------------------------------------------------

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, BACKOFF_CAP_S)
        delay = min(BACKOFF_BASE_S * 2 ** (attempt - 1), BACKOFF_CAP_S)
        return delay + random.uniform(0, BACKOFF_BASE_S)

    def _request(
        self,
        http_method: str,
        path: str,
        operation: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> RawResponse:
        url = f"{self.base_url}{path}"
        for attempt in range(1, self.max_attempts + 1):
            retry_after: float | None = None
            requested_at = datetime.now(timezone.utc)
            try:
                resp = self._client.request(http_method, path, params=params, json=json_body)
            except httpx.TimeoutException as exc:
                failure: MorphoAPIError = MorphoTimeoutError(
                    f"timeout after {self.timeout_s}s: {exc}", url=url, attempts=attempt
                )
            except httpx.TransportError as exc:
                failure = MorphoTransportError(f"transport error: {exc}", url=url, attempts=attempt)
            else:
                body = resp.content
                if 200 <= resp.status_code < 300:
                    return self._build_raw(
                        resp, body, http_method, url, operation, params, json_body, attempt, requested_at
                    )
                failure = MorphoHTTPError(
                    "HTTP error",
                    url=url,
                    status_code=resp.status_code,
                    body_excerpt=_excerpt(body),
                    attempts=attempt,
                )
                if resp.status_code not in RETRYABLE_STATUS:
                    raise failure
                retry_after = _parse_retry_after(resp.headers.get("retry-after"))

            if attempt == self.max_attempts:
                raise failure
            delay = self._backoff(attempt, retry_after)
            logger.warning("morpho_api %s failed (attempt %d/%d), retrying in %.2fs: %s",
                           operation, attempt, self.max_attempts, delay, failure)
            self._sleep(delay)
        raise AssertionError("unreachable")

    def _build_raw(
        self,
        resp: httpx.Response,
        body: bytes,
        http_method: str,
        url: str,
        operation: str,
        params: dict[str, str] | None,
        json_body: dict[str, Any] | None,
        attempts: int,
        requested_at: datetime,
    ) -> RawResponse:
        retrieved_at = datetime.now(timezone.utc)
        try:
            payload = json.loads(body, parse_float=Decimal)
        except (ValueError, UnicodeDecodeError) as exc:
            raise MorphoResponseShapeError(
                f"invalid JSON: {exc}", url=url, status_code=resp.status_code,
                body_excerpt=_excerpt(body), attempts=attempts,
            ) from exc
        return RawResponse(
            source_id=SOURCE_ID,
            operation=operation,
            http_method=http_method,
            url=url,
            request_params=dict(params or {}),
            request_body=json_body,
            status_code=resp.status_code,
            requested_at=requested_at,
            retrieved_at=retrieved_at,
            payload=payload,
            payload_sha256=hashlib.sha256(body).hexdigest(),
            attempts=attempts,
            raw_body=body,
        )

    # -- REST: markets -----------------------------------------------------

    def list_markets_page(
        self,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        collateral_token: str | None = None,
        loan_token: str | None = None,
    ) -> MarketsPage:
        """One page of Morpho Blue markets on chain_id=1 (raw items).

        collateral_token / loan_token are server-side REST filters (verified against the
        live API on 2026-10-10; they are part of the cursor's filter signature).
        """
        if not 1 <= limit <= MAX_PAGE_LIMIT:
            raise ValueError(f"limit must be within [1, {MAX_PAGE_LIMIT}]")
        params = {"chain_id": str(CHAIN_ID), "limit": str(limit)}
        if collateral_token is not None:
            params["collateral_token"] = normalize_address(collateral_token)
        if loan_token is not None:
            params["loan_token"] = normalize_address(loan_token)
        if cursor is not None:
            params["cursor"] = cursor
        raw = self._request("GET", MARKETS_PATH, "rest:/v1/blue/markets", params=params)

        payload = raw.payload
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise MorphoResponseShapeError(
                "expected object with list `data`", url=raw.url, status_code=raw.status_code,
                body_excerpt=_excerpt(raw.raw_body),
            )
        if "cursor" not in payload:
            raise MorphoResponseShapeError("missing `cursor` key", url=raw.url, status_code=raw.status_code)
        next_cursor = payload["cursor"]
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise MorphoResponseShapeError("`cursor` must be string or null", url=raw.url)

        for item in payload["data"]:
            if not isinstance(item, dict):
                raise MorphoResponseShapeError("market item is not an object", url=raw.url)
            if item.get("chain_id") != CHAIN_ID:
                raise MorphoResponseShapeError(
                    f"item chain_id={item.get('chain_id')!r} but requested {CHAIN_ID}", url=raw.url
                )
        return MarketsPage(response=raw, items=tuple(payload["data"]), next_cursor=next_cursor or None)

    def list_markets(
        self,
        limit: int = DEFAULT_PAGE_LIMIT,
        max_pages: int = DEFAULT_MAX_PAGES,
        collateral_token: str | None = None,
        loan_token: str | None = None,
    ) -> MarketListing:
        """All Morpho Blue markets on chain_id=1, following the cursor.

        A failing page stops paging; pages already fetched are kept and the error is
        returned in `errors` (complete=False).
        """
        if max_pages < 1:
            raise ValueError("max_pages must be >= 1")
        pages: list[MarketsPage] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        previous_first_id: Any = None

        for _ in range(max_pages):
            try:
                page = self.list_markets_page(
                    cursor=cursor, limit=limit, collateral_token=collateral_token, loan_token=loan_token
                )
            except MorphoAPIError as exc:
                logger.error("morpho_api page %d failed: %s", len(pages), exc)
                return MarketListing(tuple(pages), (exc,), False, f"page {len(pages)} failed")
            first_id = page.items[0].get("market_id") if page.items else None
            if pages and first_id is not None and first_id == previous_first_id:
                exc = MorphoPaginationError(
                    "server returned the same page again (cursor ignored)", url=page.response.url
                )
                return MarketListing(tuple(pages), (exc,), False, "cursor ignored")
            pages.append(page)
            if not page.items:
                return MarketListing(tuple(pages), (), True, "empty page")
            if page.next_cursor is None:
                return MarketListing(tuple(pages), (), True, "null cursor")
            if page.next_cursor in seen_cursors:
                exc = MorphoPaginationError("cursor cycle detected", url=page.response.url)
                return MarketListing(tuple(pages), (exc,), False, "cursor cycle")
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor
            previous_first_id = first_id

        logger.warning("morpho_api list_markets stopped at max_pages=%d: listing incomplete", max_pages)
        return MarketListing(tuple(pages), (), False, f"max_pages={max_pages} reached")

    # -- GraphQL ------------------------------------------------------------

    def graphql(self, query: str, operation: str) -> RawResponse:
        """POST a GraphQL query. Raises on `errors`; returns the raw response otherwise."""
        raw = self._request("POST", GRAPHQL_PATH, f"graphql:{operation}", json_body={"query": query})
        payload = raw.payload
        if not isinstance(payload, dict):
            raise MorphoResponseShapeError("GraphQL response is not an object", url=raw.url)
        if payload.get("errors"):
            raise MorphoGraphQLError(
                f"GraphQL errors in {operation}: {payload['errors']}",
                errors=payload["errors"],
                partial_data=payload.get("data"),
                response=raw,
            )
        if "data" not in payload:
            raise MorphoResponseShapeError("GraphQL response without `data`", url=raw.url)
        return raw

    def fetch_market_details(self, market_id: str) -> RawResponse:
        """Market state via GraphQL `marketById` (docs-derived query, see module doc)."""
        query = MARKET_DETAILS_QUERY % {"market_id": normalize_bytes32(market_id), "chain_id": CHAIN_ID}
        return self.graphql(query, "marketById")

    def fetch_oracle_state(self, market_id: str) -> RawResponse:
        return self._run_optional_query(
            ORACLE_STATE_QUERY, "oracle_state", market_id,
            hint="the authoritative Morpho oracle price is IOracle.price() read via RPC",
        )

    def fetch_market_history(
        self, market_id: str, start_ts: int, end_ts: int, interval: str = "DAY"
    ) -> RawResponse:
        """historicalState supplyAssets / borrowAssets (raw BigInt) / utilization."""
        return self._run_optional_query(
            MARKET_HISTORY_QUERY, "market_history", market_id, start_ts, end_ts, interval=interval
        )

    def fetch_apy_history(
        self, market_id: str, start_ts: int, end_ts: int, interval: str = "DAY"
    ) -> RawResponse:
        """historicalState supplyApy / borrowApy (ratios)."""
        return self._run_optional_query(
            APY_HISTORY_QUERY, "apy_history", market_id, start_ts, end_ts, interval=interval
        )

    def _run_optional_query(
        self,
        template: str | None,
        operation: str,
        market_id: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
        hint: str = "",
        interval: str = "DAY",
    ) -> RawResponse:
        if template is None:
            raise MorphoEndpointUnavailable(
                f"{operation}: no verified Morpho endpoint/query configured"
                + (f"; {hint}" if hint else "")
            )
        if start_ts is not None and end_ts is not None and not 0 <= start_ts < end_ts:
            raise ValueError("require 0 <= start_ts < end_ts")
        if interval not in HISTORY_INTERVALS:
            raise ValueError(f"interval must be one of {sorted(HISTORY_INTERVALS)}")
        query = template % {
            "market_id": normalize_bytes32(market_id),
            "chain_id": CHAIN_ID,
            "start_ts": start_ts if start_ts is not None else 0,
            "end_ts": end_ts if end_ts is not None else 0,
            "interval": interval,
        }
        return self.graphql(query, operation)

    def fetch_schema_type(self, type_name: str) -> RawResponse:
        """GraphQL introspection of one type — used to verify field names before use."""
        if not _GRAPHQL_NAME_RE.match(type_name):
            raise ValueError(f"invalid GraphQL type name: {type_name!r}")
        return self.graphql(SCHEMA_TYPE_QUERY % {"type_name": type_name}, f"__type:{type_name}")


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def list_markets(limit: int = DEFAULT_PAGE_LIMIT, max_pages: int = DEFAULT_MAX_PAGES) -> MarketListing:
    with MorphoApiClient() as client:
        return client.list_markets(limit=limit, max_pages=max_pages)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _show(item: dict[str, Any], key: str) -> str:
    return repr(item[key]) if key in item else "NOT PRESENT"


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Morpho API adapter smoke test (chain_id=1)")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--details", metavar="MARKET_ID", help="fetch GraphQL market state")
    parser.add_argument("--schema", metavar="TYPE", help="introspect a GraphQL type, e.g. Market")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    with MorphoApiClient() as client:
        print(f"source_id={SOURCE_ID} base_url={client.base_url} chain_id={CHAIN_ID}")
        try:
            if args.schema:
                raw = client.fetch_schema_type(args.schema)
                type_info = raw.payload["data"].get("__type")
                names = [f["name"] for f in (type_info or {}).get("fields") or []]
                print(f"retrieved_at={raw.retrieved_at.isoformat()} type={args.schema}")
                print("fields:", names if type_info else "TYPE NOT FOUND")
                return 0

            if args.details:
                raw = client.fetch_market_details(args.details)
                print(f"retrieved_at={raw.retrieved_at.isoformat()} evidence_id={raw.evidence_id}")
                print(json.dumps(raw.payload["data"], indent=2, default=str))
                return 0

            listing = client.list_markets(limit=args.limit, max_pages=args.pages)
            for err in listing.errors:
                print(f"ERROR {type(err).__name__}: {err}", file=sys.stderr)
            if not listing.pages:
                return 1
            first = listing.pages[0].response
            print(f"retrieved_at={first.retrieved_at.isoformat()} evidence_id={first.evidence_id}")
            print(f"items={listing.item_count} complete={listing.complete} ({listing.stop_reason})")
            for item, _ in listing.iter_items():
                print(
                    f"- market_id={_show(item, 'market_id')}\n"
                    f"  loan_token={_show(item, 'loan_token')} collateral_token={_show(item, 'collateral_token')}\n"
                    f"  oracle={_show(item, 'oracle_address')} irm={_show(item, 'irm_address')} "
                    f"lltv_wad={_show(item, 'lltv_wad')}"
                )
        except MorphoAPIError as exc:
            print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())