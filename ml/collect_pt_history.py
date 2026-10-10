"""Collect official Pendle daily PT USD closes; never run model inference.

Run at repository root: python -m ml.collect_pt_history --days 90
A candle starting at D becomes an observation at D+1 (close availability).
Incomplete daily candles are excluded. No prices are interpolated or invented.
"""
import argparse
import csv
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.models.evidence import (  # noqa: E402
    DataMode, DataStatus, EvidenceDTO, SourceKind, make_evidence_id,
)
from app.models.timeseries import TimeseriesDTO  # noqa: E402
from app.storage.parquet_store import ParquetStore, observations_from_series  # noqa: E402
from app.storage.sqlite_store import SqliteStore, default_data_dir  # noqa: E402

BASE_URL = "https://api-v2.pendle.finance/core"


def parse_daily_closes(payload: dict, *, available_by: datetime, evidence_id: str):
    """Exact CSV Decimal strings; reject unexpected units, duplicate/off-grid days."""
    if available_by.tzinfo is None:
        raise ValueError("available_by must be UTC aware")
    if payload.get("currency") != "USD" or payload.get("timeFrame") != "day":
        raise ValueError("expected official USD daily OHLCV payload")
    rows = list(csv.DictReader(io.StringIO(payload["results"])))
    if payload.get("total") != len(rows):
        raise ValueError("OHLCV count mismatch")
    points, excluded, seen = [], 0, set()
    for row in rows:
        stamp = int(row["time"])
        if stamp % 86400 or stamp in seen:
            raise ValueError("duplicate or off-grid daily candle")
        seen.add(stamp)
        candle_end = datetime.fromtimestamp(stamp, timezone.utc) + timedelta(days=1)
        if candle_end > available_by:
            excluded += 1
            continue
        value = Decimal(row["close"])
        if not value.is_finite() or value <= 0:
            raise ValueError("invalid daily PT close price")
        points.append(dict(timestamp=candle_end.isoformat(), value=row["close"],
                           status="OK", evidence_ids=[evidence_id]))
    points.sort(key=lambda p: p["timestamp"])
    return points, excluded


def main():
    import httpx

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()
    if not 1 <= args.days <= 1000:
        parser.error("--days must be between 1 and 1000")
    manifest = json.loads((BACKEND / "app/fixtures/demo_strategy_manifest.json").read_text())
    if manifest.get("chainId") != 1 or manifest.get("status") != "VERIFIED_ACTIVE":
        raise ValueError("requires mainnet VERIFIED_ACTIVE strategy manifest")
    pt = manifest["ptTokenAddress"].lower()
    now = datetime.now(timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=args.days)
    path = f"/v4/1/prices/{pt}/ohlcv"
    params = dict(time_frame="day", timestamp_start=start.isoformat(), timestamp_end=end.isoformat())
    with httpx.Client(timeout=30) as client:
        response = client.get(BASE_URL + path, params=params)
        response.raise_for_status()
    retrieved_at = datetime.now(timezone.utc)
    payload = response.json()
    evidence_id = make_evidence_id("pendle_api", "rest:/v4/prices/ohlcv",
                                   params, fetched_at=retrieved_at)
    points, excluded = parse_daily_closes(payload, available_by=end, evidence_id=evidence_id)
    if not points:
        raise ValueError("no completed daily candles returned")
    raw_hash = hashlib.sha256(response.content).hexdigest()
    output = default_data_dir() / "ml" / "pt_usd" / retrieved_at.strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=False)
    (output / "raw_ohlcv.json").write_bytes(response.content)
    (output / "strategy_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    series = TimeseriesDTO.model_validate(dict(
        chain_id=1, data_mode="REAL", series_id="ts_pendle_pt_usd_day_" + pt[2:],
        metric="MARKET_PRICE", subject_kind="ASSET", subject_id=pt,
        unit="USD", pricing_method="API_SPOT", interval_seconds=86400,
        source_id="pendle_api", status="OK", points=points,
    ))
    evidence = EvidenceDTO(
        chain_id=1, data_mode=DataMode.REAL, evidence_id=evidence_id,
        source_kind=SourceKind.PENDLE_API, source_id="pendle_api",
        method="rest:/v4/prices/ohlcv", request_params=params,
        target_address=pt, fetched_at=retrieved_at, payload_sha256=raw_hash,
        status=DataStatus.OK,
    )
    (output / "series.json").write_text(series.model_dump_json(indent=2) + "\n")
    (output / "evidence.json").write_text(evidence.model_dump_json(indent=2) + "\n")
    with SqliteStore() as store:
        store.save_evidence([evidence])
    parquet = ParquetStore().write_observations(
        observations_from_series(series, f"asset:1:{pt}", retrieved_at),
        DataMode.REAL, expected_interval_seconds=86400,
    )
    gaps = sum(b.timestamp-a.timestamp != timedelta(days=1)
               for a, b in zip(series.points, series.points[1:]))
    report = dict(
        status="INSUFFICIENT_HISTORY" if len(points) < 66 or gaps else "READY_FOR_DATA_AUDIT",
        days_requested=args.days, observations=len(points), gaps=gaps,
        first_observation_at=points[0]["timestamp"], latest_observation_at=points[-1]["timestamp"],
        source_as_of=points[-1]["timestamp"], retrieved_at=retrieved_at.isoformat(),
        source_url=str(response.url), source_snapshot_ref=f"sha256:{raw_hash}",
        entity_id=f"asset:1:{pt}", pendle_market_address=manifest["pendleMarketAddress"],
        expiry=manifest["expiry"], excluded_incomplete_candles=excluded,
        parquet_path=str(parquet), export_directory=str(output),
        warnings=["Daily USD close, timestamps are candle end, not candle start",
                  "API collection is retrospective; original price availability/revisions not audited",
                  "No interpolation by our collector; provider prices on days without reported trades require review",
                  "Raw API prices have provider precision, not on-chain accounting precision"],
    )
    (output / "collection_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
