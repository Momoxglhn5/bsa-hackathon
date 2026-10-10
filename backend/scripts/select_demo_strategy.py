"""Select one live Morpho Blue × Pendle PT candidate strategy for the demo.

Flow:
  1. list Ethereum Morpho Blue markets that have a collateral token
  2. list active Ethereum Pendle markets (PT + expiry)
  3. normalise both into MarketDTOs
  4. join on chain_id + Morpho collateral address == Pendle PT address
  5. rank: active (not expired) candidates, USDC loan asset first (by address)
  6. keep the first candidate whose required metrics and history are available
  7. write backend/app/fixtures/demo_strategy_manifest.json with status CANDIDATE

No symbol is used for matching or selection. Nothing is marked verified:
verifiedBlock / verifiedAt stay null until the RPC verification step runs.

Run from backend/:  python scripts/select_demo_strategy.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.adapters.morpho_api import MorphoAPIError, MorphoApiClient  # noqa: E402
from app.adapters.pendle_api import PendleAPIError, PendleApiClient  # noqa: E402
from app.ingestion.join import JoinCandidate, find_candidate_strategies  # noqa: E402
from app.ingestion.normalize import (  # noqa: E402
    NormalizationError,
    SourceRef,
    normalize_morpho_market,
    normalize_pendle_history,
    normalize_pendle_market,
)
from app.models.evidence import ZERO_ADDRESS, normalize_address  # noqa: E402
from app.models.market import MarketDTO  # noqa: E402
from app.models.timeseries import TimeseriesMetric  # noqa: E402

MANIFEST_PATH = BACKEND_DIR / "app" / "fixtures" / "demo_strategy_manifest.json"
CHAIN_ID = 1

logger = logging.getLogger("select_demo_strategy")


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

def load_morpho(client: MorphoApiClient, max_pages: int) -> tuple[list[MarketDTO], dict[str, str], list[str]]:
    listing = client.list_markets(limit=100, max_pages=max_pages)
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
        try:
            nm = normalize_morpho_market(item, SourceRef.from_response(raw))
        except (NormalizationError, ValueError) as exc:
            issues.append(f"morpho item {item.get('market_id')!r} not normalised: {exc}")
            continue
        markets.append(nm.dto)
        urls[nm.key] = full_url(raw)
    logger.info("morpho: %d markets with collateral, %d without", len(markets), skipped_no_collateral)
    return markets, urls, issues


def load_pendle(client: PendleApiClient, max_pages: int) -> tuple[list[MarketDTO], dict[str, str], list[str]]:
    listing = client.list_markets(limit=100, max_pages=max_pages, is_active=True)
    issues = [f"pendle listing: {e}" for e in listing.errors]
    if not listing.complete:
        issues.append(f"pendle listing incomplete ({listing.stop_reason})")
    markets: list[MarketDTO] = []
    urls: dict[str, str] = {}
    for item, raw in listing.iter_items():
        try:
            nm = normalize_pendle_market(item, SourceRef.from_response(raw))
        except (NormalizationError, ValueError) as exc:
            issues.append(f"pendle item {item.get('address')!r} not normalised: {exc}")
            continue
        markets.append(nm.dto)
        urls[nm.key] = full_url(raw)
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
# Main
# ---------------------------------------------------------------------------

def build_manifest(cand: JoinCandidate, morpho_dto: MarketDTO, source_urls: list[str]) -> dict[str, Any]:
    s = cand.strategy
    return {
        "chainId": s.chain_id,
        "morphoMarketId": s.morpho_market_id,
        "pendleMarketAddress": s.pendle_market_address,
        "ptTokenAddress": s.pt.address,
        "loanTokenAddress": s.loan_asset.address,
        "oracleAddress": morpho_dto.morpho.oracle_address,
        "expiry": s.expiry.isoformat().replace("+00:00", "Z") if s.expiry else None,
        "verifiedBlock": None,
        "verifiedAt": None,
        "sourceUrls": list(dict.fromkeys(source_urls)),
        "status": s.status.value,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Select a demo Morpho × Pendle candidate strategy (chain_id=1)")
    parser.add_argument("--dry-run", action="store_true", help="print only, do not write the manifest")
    parser.add_argument("--max-checked", type=int, default=10, help="candidates to check before giving up")
    parser.add_argument("--min-days-to-expiry", type=int, default=7)
    parser.add_argument("--history-days", type=int, default=14)
    parser.add_argument("--min-history-points", type=int, default=3)
    parser.add_argument("--require-morpho-state", action="store_true",
                        help="reject candidates whose Morpho API state is unavailable")
    parser.add_argument("--morpho-max-pages", type=int, default=100)
    parser.add_argument("--pendle-max-pages", type=int, default=30)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    now = datetime.now(timezone.utc)

    with MorphoApiClient() as morpho, PendleApiClient() as pendle:
        morpho_markets, morpho_urls, issues = load_morpho(morpho, args.morpho_max_pages)
        pendle_markets, pendle_urls, p_issues = load_pendle(pendle, args.pendle_max_pages)
        issues += p_issues
        for issue in issues:
            logger.warning(issue)

        result = find_candidate_strategies(morpho_markets, pendle_markets, as_of=now)
        print(f"\nmorpho markets={result.morpho_markets_seen} pendle markets={result.pendle_markets_seen} "
              f"candidates={len(result.candidates)} expired={len(result.expired)} "
              f"anomalies={len(result.anomalies)}")
        for a in result.anomalies:
            print(f"  anomaly: {a}")
        if not result.candidates:
            print("No active candidate found.", file=sys.stderr)
            return 2

        by_key = {m.market_key: m for m in morpho_markets + pendle_markets}
        print("\nRanked candidates (USDC loan first, by address):")
        for i, c in enumerate(result.candidates[: args.max_checked], 1):
            s = c.strategy
            print(f"  {i}. {c.morpho_market_key}\n"
                  f"     pendle={s.pendle_market_address} pt={s.pt.address} loan={s.loan_asset.address}"
                  f"{' [USDC]' if c.preferred_loan else ''} expiry={s.expiry} data_status={s.data_status.value}")

        selected: tuple[JoinCandidate, MarketDTO, list[str]] | None = None
        for c in result.candidates[: args.max_checked]:
            if c.pendle_market_key is None:
                print(f"\n  skip {c.morpho_market_key}: Pendle market ambiguous")
                continue
            morpho_dto, pendle_dto = by_key[c.morpho_market_key], by_key[c.pendle_market_key]
            ok, problems, extra_urls = check_candidate(
                c, pendle_dto, morpho_dto, pendle, morpho, now,
                args.min_days_to_expiry, args.history_days, args.min_history_points,
                args.require_morpho_state,
            )
            if ok:
                urls = [morpho_urls[c.morpho_market_key], pendle_urls[c.pendle_market_key], *extra_urls]
                selected = (c, morpho_dto, urls)
                break
            print(f"\n  reject {c.morpho_market_key}:")
            for p in problems:
                print(f"    - {p}")

    if selected is None:
        print("\nNo candidate passed the availability checks.", file=sys.stderr)
        return 2

    cand, morpho_dto, urls = selected
    manifest = build_manifest(cand, morpho_dto, urls)
    print("\nSelected candidate (NOT verified on-chain yet):")
    print(json.dumps(manifest, indent=2))
    print(f"data_status={cand.strategy.data_status.value} "
          f"evidence_ids={list(cand.strategy.evidence_ids)}")

    if args.dry_run:
        print("\n--dry-run: manifest not written")
        return 0
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())