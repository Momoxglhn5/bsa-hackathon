"""Backfill REAL histories of the demo strategy into the Parquet store.

Sources (independent — one failing never stops the others; partial history is allowed):
  * pendle_history         Pendle /v3 historical-data  -> PT_IMPLIED_APY, UNDERLYING_APY
  * morpho_market_history  Morpho historicalState      -> TOTAL_SUPPLY/BORROW_ASSETS, UTILIZATION
  * morpho_apy_history     Morpho historicalState      -> SUPPLY_APY, BORROW_APY

Missing periods are never filled or interpolated; gaps are only flagged (GAP_BEFORE).
Points after the retrieval time, or off the time-frame grid (Morpho appends the live,
intra-period state), are dropped and counted. Each API call's evidence goes to SQLite.

Run from backend/:  python scripts/backfill_histories.py [--days 30] [--time-frame day|hour] [--dry-run]
Exit 0 if at least the Pendle history was written (or produced, with --dry-run).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.adapters.morpho_api import MorphoApiClient  # noqa: E402
from app.adapters.pendle_api import PendleApiClient  # noqa: E402
from app.ingestion.backfill import (  # noqa: E402
    INTERVALS,
    PENDLE_SOURCE,
    BackfillResult,
    run_backfill,
)
from app.models.market import TokenRef  # noqa: E402
from app.storage.parquet_store import ParquetStore  # noqa: E402
from app.storage.sqlite_store import SqliteStore  # noqa: E402

MANIFEST_PATH = BACKEND_DIR / "app" / "fixtures" / "demo_strategy_manifest.json"
logger = logging.getLogger("backfill_histories")


def _read_loan_decimals(address: str) -> int | None:
    """ERC20 decimals() via RPC; None (with a warning) if unreachable."""
    from app.adapters.ethereum_rpc import ERC20_DECIMALS_ABI, EthereumRPCError, EthereumRpcClient

    try:
        rpc = EthereumRpcClient()
        return int(rpc.call(address, ERC20_DECIMALS_ABI, "decimals", [], rpc.pin_block()).value)
    except EthereumRPCError as exc:
        logger.warning("loan token decimals unavailable via RPC: %s", exc)
        return None


def _iso(dt: datetime | None) -> str:
    return dt.isoformat().replace("+00:00", "Z") if dt else "-"


def print_report(result: BackfillResult) -> None:
    print("\nseries (entity, metric):")
    for r in result.series:
        print(f"- {r.entity_id} {r.metric} [{r.source_id}]: written={r.written} with_value={r.with_value} "
              f"missing={r.missing} gap_before={r.gap_before} first={_iso(r.first_ts)} last={_iso(r.last_ts)}"
              + (f" dropped_future={r.dropped_future}" if r.dropped_future else "")
              + (f" dropped_off_grid={r.dropped_off_grid}" if r.dropped_off_grid else ""))
    print("\nsources:")
    for s in result.sources:
        print(f"- {s.name}: {s.status}" + (f" ({s.reason})" if s.reason else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill REAL histories of the demo strategy")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--time-frame", choices=sorted(INTERVALS), default="day")
    parser.add_argument("--dry-run", action="store_true", help="fetch and normalise, write nothing")
    parser.add_argument("--allow-unverified", action="store_true",
                        help="run even if the manifest strategy is not VERIFIED_ACTIVE")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.days < 1:
        parser.error("--days must be >= 1")

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("status") != "VERIFIED_ACTIVE" and not args.allow_unverified:
        print(f"manifest status is {manifest.get('status')!r}, not VERIFIED_ACTIVE "
              "(use --allow-unverified to override)", file=sys.stderr)
        return 2

    loan_token = TokenRef(
        chain_id=manifest["chainId"], address=manifest["loanTokenAddress"],
        decimals=_read_loan_decimals(manifest["loanTokenAddress"]),
    )
    now = datetime.now(timezone.utc)
    sqlite = None if args.dry_run else SqliteStore()
    try:
        with MorphoApiClient() as morpho, PendleApiClient() as pendle:
            result = run_backfill(
                manifest, pendle=pendle, morpho=morpho, parquet=ParquetStore(), sqlite=sqlite,
                loan_token=loan_token, now=now, days=args.days, time_frame=args.time_frame,
                dry_run=args.dry_run,
            )
    finally:
        if sqlite is not None:
            sqlite.close()
    print_report(result)
    if args.dry_run:
        print("\n--dry-run: nothing written")
    return 0 if result.wrote(PENDLE_SOURCE) else 1


if __name__ == "__main__":
    raise SystemExit(main())
