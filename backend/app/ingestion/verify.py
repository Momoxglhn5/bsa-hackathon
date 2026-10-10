"""Strategy discovery inputs and on-chain verification, shared by scripts and refresh.

load_morpho / load_pendle list and normalise markets (keeping each raw API item for
the API-vs-chain checks), check_candidate checks data availability, verify_onchain
reads Morpho params, Pendle tokens + expiry and SY info at ONE pinned block, and
verify_and_promote chains them into promote_strategy. No symbol is used anywhere.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from app.adapters.ethereum_rpc import (
    EthereumRPCError,
    EthereumRpcClient,
    read_morpho_market_params,
    read_pendle_market_tokens,
    read_sy_asset_info,
    verify_pt_matches_collateral,
)
from app.adapters.morpho_api import MorphoAPIError, MorphoApiClient
from app.adapters.pendle_api import PendleAPIError, PendleApiClient
from app.ingestion.join import JoinCandidate, promote_strategy
from app.ingestion.normalize import (
    NormalizationError,
    SourceRef,
    build_strategy_relations,
    morpho_param_checks,
    normalize_morpho_market,
    normalize_pendle_history,
    normalize_pendle_market,
    pendle_sy_yield_token_check,
    pendle_token_checks,
)
from app.models.evidence import ZERO_ADDRESS, RelationDTO, VerificationCheckDTO, normalize_address
from app.models.market import MarketDTO
from app.models.strategy import StrategyDTO
from app.models.timeseries import TimeseriesMetric

logger = logging.getLogger(__name__)

# market key -> (raw API item, its RawResponse); needed for the API-vs-chain checks
# and to store the API evidence.
RawItems = dict[str, tuple[dict[str, Any], Any]]


def full_url(raw: Any) -> str:
    return f"{raw.url}?{urlencode(raw.request_params)}" if raw.request_params else raw.url


def _has_collateral(item: dict[str, Any]) -> bool:
    try:
        return normalize_address(item.get("collateral_token")) != ZERO_ADDRESS
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Steps 1-3: list + normalise
# ---------------------------------------------------------------------------

def load_morpho(
    client: MorphoApiClient, max_pages: int, raws: RawItems, collateral_token: str | None = None
) -> tuple[list[MarketDTO], dict[str, str], list[str]]:
    """collateral_token: server-side filter (one page instead of ~20 for a known strategy)."""
    listing = client.list_markets(limit=100, max_pages=max_pages, collateral_token=collateral_token)
    issues = [f"morpho listing: {e}" for e in listing.errors]
    if not listing.complete:
        issues.append(f"morpho listing incomplete ({listing.stop_reason})")
    markets: list[MarketDTO] = []
    urls: dict[str, str] = {}
    skipped_no_collateral = 0
    for item, raw in listing.iter_items():
        if not _has_collateral(item):
            skipped_no_collateral += 1
            continue
        ref = SourceRef.from_response(raw)
        try:
            nm = normalize_morpho_market(item, ref)
        except (NormalizationError, ValueError) as exc:
            issues.append(f"morpho item {item.get('market_id')!r} not normalised: {exc}")
            continue
        markets.append(nm.dto)
        urls[nm.key] = full_url(raw)
        raws[nm.key] = (item, raw)
    logger.info("morpho: %d markets with collateral, %d without", len(markets), skipped_no_collateral)
    return markets, urls, issues


def load_pendle(
    client: PendleApiClient, max_pages: int, raws: RawItems
) -> tuple[list[MarketDTO], dict[str, str], list[str]]:
    listing = client.list_markets(limit=100, max_pages=max_pages, is_active=True)
    issues = [f"pendle listing: {e}" for e in listing.errors]
    if not listing.complete:
        issues.append(f"pendle listing incomplete ({listing.stop_reason})")
    markets: list[MarketDTO] = []
    urls: dict[str, str] = {}
    for item, raw in listing.iter_items():
        ref = SourceRef.from_response(raw)
        try:
            nm = normalize_pendle_market(item, ref)
        except (NormalizationError, ValueError) as exc:
            issues.append(f"pendle item {item.get('address')!r} not normalised: {exc}")
            continue
        markets.append(nm.dto)
        urls[nm.key] = full_url(raw)
        raws[nm.key] = (item, raw)
    logger.info("pendle: %d active markets", len(markets))
    return markets, urls, issues


# ---------------------------------------------------------------------------
# Step 7: availability checks (data presence only — no risk logic)
# ---------------------------------------------------------------------------

def check_candidate(
    cand: JoinCandidate,
    pendle_dto: MarketDTO,
    morpho_dto: MarketDTO,
    pendle: PendleApiClient,
    morpho: MorphoApiClient,
    now: datetime,
    min_days_to_expiry: int,
    history_days: int,
    min_history_points: int,
    require_morpho_state: bool,
) -> tuple[bool, list[str], list[str]]:
    """Returns (ok, problems, extra source URLs)."""
    problems: list[str] = []
    urls: list[str] = []
    s = cand.strategy
    p = pendle_dto.pendle

    if s.expiry is None or s.expiry <= now + timedelta(days=min_days_to_expiry):
        problems.append(f"expiry {s.expiry} within {min_days_to_expiry} days")
    if morpho_dto.morpho.oracle_address == ZERO_ADDRESS:
        problems.append("Morpho market has no oracle")
    for label, value in (("pendle impliedApy", p.implied_apy), ("pendle liquidity", p.liquidity_usd)):
        if value is None:
            problems.append(f"{label} missing")

    try:
        hist = pendle.fetch_market_history(
            p.market_address, "day", now - timedelta(days=history_days), now
        )
        urls.append(full_url(hist))
        series = normalize_pendle_history(
            hist.payload, p.market_address, SourceRef.from_response(hist), time_frame="day"
        )
        implied = series[TimeseriesMetric.PT_IMPLIED_APY].dto
        with_value = sum(1 for pt in implied.points if pt.value is not None)
        if with_value < min_history_points:
            problems.append(f"implied APY history: {with_value} points with value (< {min_history_points})")
    except (PendleAPIError, NormalizationError, ValueError) as exc:
        problems.append(f"pendle history unavailable: {exc}")

    try:
        details = morpho.fetch_market_details(s.morpho_market_id)
        urls.append(details.url)
        state = ((details.payload.get("data") or {}).get("marketById") or {}).get("state") or {}
        absent = [k for k in ("supplyAssets", "borrowAssets") if state.get(k) is None]
        if absent and require_morpho_state:
            problems.append(f"morpho API state missing {absent}")
        elif absent:
            logger.warning("%s: morpho API state missing %s (RPC will provide it)", cand.morpho_market_key, absent)
    except (MorphoAPIError, ValueError) as exc:
        if require_morpho_state:
            problems.append(f"morpho API state unavailable: {exc}")
        else:
            logger.warning("%s: morpho API state unavailable (RPC will provide it): %s",
                           cand.morpho_market_key, str(exc)[:200])

    return not problems, problems, urls


# ---------------------------------------------------------------------------
# Step 7: on-chain verification at one pinned block
# ---------------------------------------------------------------------------

@dataclass
class Verification:
    checks: tuple[VerificationCheckDTO, ...] = ()
    block_number: int | None = None
    block_hash: str | None = None
    block_time: datetime | None = None
    evidence_ids: tuple[str, ...] = ()
    onchain_expiry: datetime | None = None
    relations: tuple[RelationDTO, ...] = ()
    error: str | None = None


def verify_onchain(
    rpc: EthereumRpcClient | None, rpc_error: str | None, cand: JoinCandidate, raws: RawItems
) -> Verification:
    """RPC failure -> Verification with `error` (and no block): the caller keeps CANDIDATE."""
    if rpc is None:
        return Verification(error=rpc_error or "RPC client unavailable")
    s = cand.strategy
    morpho_item, morpho_raw = raws[cand.morpho_market_key]
    pendle_item, pendle_raw = raws[cand.pendle_market_key]
    morpho_ref, pendle_ref = SourceRef.from_response(morpho_raw), SourceRef.from_response(pendle_raw)
    try:
        pinned = rpc.pin_block()
        params = read_morpho_market_params(rpc, s.morpho_market_id, pinned)
        tokens, expiry_ev = read_pendle_market_tokens(rpc, s.pendle_market_address, pinned)
        sy_info = read_sy_asset_info(rpc, tokens.sy, pinned)
    except EthereumRPCError as exc:
        return Verification(error=f"{type(exc).__name__}: {exc}")

    checks = (
        morpho_param_checks(morpho_item, morpho_ref, params)
        + pendle_token_checks(pendle_item, pendle_ref, tokens)
        + (pendle_sy_yield_token_check(pendle_item, pendle_ref, sy_info),
           verify_pt_matches_collateral(params, tokens))
    )
    relations = tuple(build_strategy_relations(params, tokens, sy_info, rpc.evidence))
    rpc_ids = (params.ref.evidence_id, tokens.ref.evidence_id, expiry_ev.evidence_id,
               sy_info.yield_token_ref.evidence_id, sy_info.ref.evidence_id)
    return Verification(
        checks=checks,
        block_number=pinned.number,
        block_hash=pinned.hash,
        block_time=pinned.timestamp,
        evidence_ids=(morpho_ref.evidence_id, pendle_ref.evidence_id, *rpc_ids),
        onchain_expiry=datetime.fromtimestamp(tokens.expiry_ts, tz=timezone.utc),
        relations=relations,
    )


def find_listing_item(listing: Any, field: str, value: str) -> tuple[dict[str, Any], Any] | None:
    """(item, raw) whose `field` equals `value` (lowercase compare), else None."""
    value = value.lower()
    return next(
        ((item, raw) for item, raw in listing.iter_items()
         if isinstance(item.get(field), str) and item[field].lower() == value),
        None,
    )


def verify_and_promote(
    cand: JoinCandidate,
    morpho_dto: MarketDTO,
    pendle_dto: MarketDTO,
    raws: RawItems,
    pendle: PendleApiClient,
    morpho: MorphoApiClient,
    rpc: EthereumRpcClient | None,
    rpc_error: str | None,
    now: datetime,
    *,
    min_days_to_expiry: int = 7,
    history_days: int = 14,
    min_history_points: int = 3,
    require_morpho_state: bool = False,
) -> tuple[StrategyDTO, Verification, list[str], list[str]]:
    """check_candidate + verify_onchain + promote_strategy.

    Returns (promoted strategy, verification, data problems, extra source URLs).
    """
    data_ok, problems, extra_urls = check_candidate(
        cand, pendle_dto, morpho_dto, pendle, morpho, now,
        min_days_to_expiry, history_days, min_history_points, require_morpho_state,
    )
    v = verify_onchain(rpc, rpc_error, cand, raws)
    promoted = promote_strategy(
        cand.strategy, v.checks, v.block_number, v.evidence_ids, v.onchain_expiry,
        data_ok, problems, now, min_days_to_expiry,
    )
    return promoted, v, problems, extra_urls
