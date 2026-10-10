"""Collect the Morpho ORACLE_PRICE history of the demo strategy for Monte Carlo PR2.

Reads IOracle.price() once per UTC midnight at the last block <= midnight
(read-only eth_call, no transaction), stores the RPC evidence in SQLite and the
points in Parquet (source ethereum_rpc). Bounded: at most 60 days, stops after
three consecutive failures. Past blocks usually need an archive-capable RPC set
in ETHEREUM_RPC_URL (environment or backend/.env, never committed).

Run from backend/:  python scripts/backfill_oracle_history.py [--days 31] [--dry-run]
Exit 0 only if every requested midnight was read.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.adapters.ethereum_rpc import EthereumRPCError, EthereumRpcClient  # noqa: E402
from app.ingestion.oracle_history import MAX_DAYS, run_oracle_backfill  # noqa: E402
from app.storage.parquet_store import ParquetStore  # noqa: E402
from app.storage.sqlite_store import SqliteStore  # noqa: E402

MANIFEST_PATH = BACKEND_DIR / "app" / "fixtures" / "demo_strategy_manifest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill the Morpho oracle price history (read-only RPC)")
    parser.add_argument("--days", type=int, default=31, help=f"UTC midnights to read, 1..{MAX_DAYS} (31 = PR2 window)")
    parser.add_argument("--dry-run", action="store_true", help="read and print, write nothing")
    args = parser.parse_args(argv)
    if not 1 <= args.days <= MAX_DAYS:
        parser.error(f"--days must be between 1 and {MAX_DAYS}")

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("status") != "VERIFIED_ACTIVE" or manifest.get("chainId") != 1:
        print("manifest strategy is not a VERIFIED_ACTIVE mainnet strategy", file=sys.stderr)
        return 2
    try:
        rpc = EthereumRpcClient()
    except EthereumRPCError as exc:
        print(f"RPC unavailable: {exc}. Set ETHEREUM_RPC_URL (archive-capable for past blocks).",
              file=sys.stderr)
        return 1
    print(f"RPC mode: {rpc.mode} (host={rpc.rpc_host})")
    sqlite = None if args.dry_run else SqliteStore()
    try:
        report = run_oracle_backfill(
            rpc, market_id=manifest["morphoMarketId"], sqlite=sqlite, parquet=ParquetStore(),
            now=datetime.now(timezone.utc), days=args.days,
            expected_collateral=manifest.get("ptTokenAddress"), expected_loan=manifest.get("loanTokenAddress"),
            dry_run=args.dry_run,
        )
    except (EthereumRPCError, ValueError) as exc:
        print(f"backfill failed before sampling: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if sqlite is not None:
            sqlite.close()

    print(f"market {report.market_id} oracle {report.oracle_address}")
    for s in report.samples:
        at = s.target_time.isoformat().replace("+00:00", "Z")
        if s.value is not None:
            print(f"- {at}: block {s.block_number} ({s.block_time:%Y-%m-%dT%H:%M:%SZ}) price {s.value}")
        else:
            print(f"- {at}: ERROR {s.error}")
    if report.stopped_reason:
        print(f"stopped: {report.stopped_reason}")
    print(f"read {report.ok_count}/{args.days} midnights; written points={report.written_points} "
          f"evidence={report.written_evidence}" + (" (--dry-run: nothing written)" if args.dry_run else ""))
    return 0 if report.ok_count == args.days else 1


if __name__ == "__main__":
    raise SystemExit(main())
