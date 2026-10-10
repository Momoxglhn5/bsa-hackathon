"""Select one live Morpho Blue × Pendle PT candidate strategy for the demo.

Flow:
  1. list Ethereum Morpho Blue markets that have a collateral token
  2. list active Ethereum Pendle markets (PT + expiry)
  3. normalise both into MarketDTOs
  4. join on chain_id + Morpho collateral address == Pendle PT address
  5. rank: active (not expired) candidates, USDC loan asset first (by address)
  6. check data availability (metrics, history)
  7. verify on-chain at ONE pinned block (Morpho params, Pendle tokens + expiry, SY info)
     and promote: keep the first candidate that becomes VERIFIED_ACTIVE
  8. write backend/app/fixtures/demo_strategy_manifest.json

No symbol is used for matching, selection or promotion.

Run from backend/:  python scripts/select_demo_strategy.py [--dry-run] [--verify-only]
--verify-only re-verifies the strategy already in the manifest (no discovery ranking).
Exit 0 only if the written strategy is VERIFIED_ACTIVE.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.adapters.ethereum_rpc import EthereumRPCError, EthereumRpcClient  # noqa: E402
from app.adapters.morpho_api import MorphoApiClient  # noqa: E402
from app.adapters.pendle_api import PendleApiClient  # noqa: E402
from app.ingestion.join import find_candidate_strategies  # noqa: E402
from app.ingestion.normalize import morpho_market_key, pendle_market_key  # noqa: E402
from app.ingestion.verify import (  # noqa: E402
    RawItems,
    Verification,
    load_morpho,
    load_pendle,
    verify_and_promote,
)
from app.models.market import MarketDTO  # noqa: E402
from app.models.strategy import StrategyDTO, StrategyStatus  # noqa: E402
from app.storage.sqlite_store import SqliteStore  # noqa: E402

MANIFEST_PATH = BACKEND_DIR / "app" / "fixtures" / "demo_strategy_manifest.json"

logger = logging.getLogger("select_demo_strategy")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def build_manifest(
    s: StrategyDTO, morpho_dto: MarketDTO, source_urls: list[str], v: Verification
) -> dict[str, Any]:
    return {
        "chainId": s.chain_id,
        "morphoMarketId": s.morpho_market_id,
        "pendleMarketAddress": s.pendle_market_address,
        "ptTokenAddress": s.pt.address,
        "loanTokenAddress": s.loan_asset.address,
        "oracleAddress": morpho_dto.morpho.oracle_address,
        "expiry": _iso(s.expiry),
        "verifiedBlock": v.block_number,
        "verifiedBlockHash": v.block_hash,
        "verifiedAt": _iso(v.block_time),
        "sourceUrls": list(dict.fromkeys(source_urls)),
        "status": s.status.value,
        "statusReason": s.status_reason,
        "dataStatus": s.data_status.value,
        "evidenceIds": list(s.evidence_ids),
        "checks": [{"name": c.check, "status": c.status.value} for c in s.verification],
        "relationsCount": len(v.relations),
    }


def _print_result(s: StrategyDTO, v: Verification) -> None:
    if v.error:
        print(f"    RPC verification failed: {v.error}")
    if v.block_number is not None:
        print(f"    pinned block={v.block_number} hash={v.block_hash} at={_iso(v.block_time)}")
    for c in s.verification:
        print(f"    - {c.check}: claimed={c.claimed_value} onchain={c.onchain_value} status={c.status.value}")
    print(f"    => status={s.status.value} data_status={s.data_status.value} reason={s.status_reason}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Select and verify a demo Morpho × Pendle strategy (chain_id=1)")
    parser.add_argument("--dry-run", action="store_true", help="print only, do not write the manifest")
    parser.add_argument("--verify-only", action="store_true",
                        help="re-verify the strategy in the existing manifest instead of discovering one")
    parser.add_argument("--no-db", action="store_true", help="do not write to the SQLite store")
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

    try:
        rpc, rpc_error = EthereumRpcClient(), None
    except EthereumRPCError as exc:
        rpc, rpc_error = None, f"{type(exc).__name__}: {exc}"
        logger.error("RPC unavailable, no candidate can be promoted: %s", rpc_error)

    raws: RawItems = {}
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8")) if args.verify_only else None
    # --verify-only: filter Morpho server-side on the manifest PT (one page, not ~20).
    collateral = manifest.get("ptTokenAddress") if manifest else None
    with MorphoApiClient() as morpho, PendleApiClient() as pendle:
        morpho_markets, morpho_urls, issues = load_morpho(
            morpho, args.morpho_max_pages, raws, collateral_token=collateral
        )
        pendle_markets, pendle_urls, p_issues = load_pendle(pendle, args.pendle_max_pages, raws)
        issues += p_issues
        for issue in issues:
            logger.warning(issue)

        if manifest is not None:
            keys =(morpho_market_key(manifest["morphoMarketId"]),
                    pendle_market_key(manifest["pendleMarketAddress"]))
            missing = [k for k in keys if k not in raws]
            if missing:
                print(f"manifest market(s) not found in the API listings: {missing}", file=sys.stderr)
                return 2
            morpho_markets = [m for m in morpho_markets if m.market_key == keys[0]]
            pendle_markets = [m for m in pendle_markets if m.market_key == keys[1]]

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

        final: tuple[StrategyDTO, MarketDTO, MarketDTO, list[str], Verification] | None = None
        for c in result.candidates[: args.max_checked]:
            if c.pendle_market_key is None:
                print(f"\n  skip {c.morpho_market_key}: Pendle market ambiguous")
                continue
            morpho_dto, pendle_dto = by_key[c.morpho_market_key], by_key[c.pendle_market_key]
            promoted, v, problems, extra_urls = verify_and_promote(
                c, morpho_dto, pendle_dto, raws, pendle, morpho, rpc, rpc_error, now,
                min_days_to_expiry=args.min_days_to_expiry, history_days=args.history_days,
                min_history_points=args.min_history_points, require_morpho_state=args.require_morpho_state,
            )
            print(f"\n  {c.morpho_market_key}:")
            for p in problems:
                print(f"    data problem: {p}")
            _print_result(promoted, v)
            urls = [morpho_urls[c.morpho_market_key], pendle_urls[c.pendle_market_key], *extra_urls]
            # --verify-only reports whatever the re-verification gives; discovery keeps looking.
            if promoted.status == StrategyStatus.VERIFIED_ACTIVE or args.verify_only:
                final = (promoted, morpho_dto, pendle_dto, urls, v)
                break

    if final is None:
        print("\nNo candidate reached VERIFIED_ACTIVE; manifest not written.", file=sys.stderr)
        return 2

    strategy, morpho_dto, pendle_dto, urls, v = final
    manifest = build_manifest(strategy, morpho_dto, urls, v)
    print(json.dumps(manifest, indent=2))
    print(f"\nfinal status={strategy.status.value} reason={strategy.status_reason}")
    if args.dry_run:
        print("--dry-run: manifest not written")
    else:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {MANIFEST_PATH}")

    if not args.no_db:
        api_evidence = [raws[k][1].to_evidence() for k in (morpho_dto.market_key, pendle_dto.market_key)]
        rpc_evidence = list(rpc.evidence.values()) if rpc is not None else []
        with SqliteStore() as store:
            written = {
                "evidence": store.save_evidence(api_evidence + rpc_evidence),
                "markets": store.save_markets([morpho_dto, pendle_dto]),
                "strategies": store.save_strategies([strategy]),
                "relations": store.save_relations(v.relations),
            }
            print(f"db={store.path} written={written} totals={store.counts()}")
    return 0 if strategy.status == StrategyStatus.VERIFIED_ACTIVE else 1


if __name__ == "__main__":
    raise SystemExit(main())
