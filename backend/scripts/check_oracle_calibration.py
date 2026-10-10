"""Check offline whether the stored oracle history supports a PR2 calibration.

Reads the Data stores exactly like the analytics API (no network), then prints
the calibration or the precise missing fields. Nothing is written.

Run from backend/:  python scripts/check_oracle_calibration.py [--data-mode REAL]
Exit 0 if calibrated, 1 if unavailable, 2 if the store has no matching market.
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

from fastapi import HTTPException  # noqa: E402

from app.analytics.provider import snapshot_from_store  # noqa: E402
from app.models.evidence import DataMode, normalize_bytes32  # noqa: E402
from app.storage.parquet_store import ParquetStore  # noqa: E402
from app.storage.sqlite_store import SqliteStore, default_db_path  # noqa: E402
from app.stress.monte_carlo.calibration import (  # noqa: E402
    MonteCarloDataUnavailable,
    calibrate_oracle_volatility,
    select_oracle_series,
)

MANIFEST_PATH = BACKEND_DIR / "app" / "fixtures" / "demo_strategy_manifest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline PR2 oracle calibration check")
    parser.add_argument("--data-mode", choices=[m.value for m in DataMode], default="REAL")
    parser.add_argument("--market-id", help="Morpho market id (default: demo manifest)")
    args = parser.parse_args(argv)
    market_id = normalize_bytes32(args.market_id or json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["morphoMarketId"])
    if not default_db_path().is_file():  # do not create an empty store just to check it
        print(f"UNAVAILABLE: no Data store at {default_db_path()}; run the Data pipeline first")
        return 2
    with SqliteStore() as store:
        try:
            snapshot = snapshot_from_store(store, ParquetStore(), DataMode(args.data_mode))
        except HTTPException as exc:
            print(f"UNAVAILABLE: {exc.detail}")
            return 2
    market = next((m for m in snapshot.markets if m.morpho is not None and m.market_id == market_id), None)
    if market is None:
        print(f"UNAVAILABLE: Morpho market {market_id} not stored in {args.data_mode}")
        return 2
    now = datetime.now(timezone.utc)
    series, missing = select_oracle_series(snapshot.series, market)
    if series is not None:
        print(f"series {series.series_id}: {len(series.points)} sampling candidates, "
              f"{series.points[0].timestamp:%Y-%m-%dT%H:%MZ} .. {series.points[-1].timestamp:%Y-%m-%dT%H:%MZ}")
    try:
        if missing:
            raise MonteCarloDataUnavailable(missing)
        calibration = calibrate_oracle_volatility(series, market, evidence=snapshot.evidence, as_of=now)
    except MonteCarloDataUnavailable as exc:
        print("status: UNAVAILABLE")
        for name in exc.missing_fields:
            print(f"- missing: {name}")
        return 1
    print("status: CALIBRATED (historical oracle variability, not a forecast)")
    print(json.dumps(calibration.model_dump(mode="json", exclude={"points"}), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
