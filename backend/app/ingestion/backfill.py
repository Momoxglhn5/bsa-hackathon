"""History backfill core: fetch, normalise and store REAL histories into Parquet.

Sources are independent (one failing never stops the others; partial history is
allowed). Missing periods are never filled or interpolated; gaps are only flagged
(GAP_BEFORE). Points after the retrieval time, or off the time-frame grid (Morpho
appends the live intra-period state), are dropped and counted.
Used by scripts/backfill_histories.py and app.ingestion.refresh.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from app.adapters.morpho_api import MorphoAPIError, MorphoEndpointUnavailable
from app.adapters.pendle_api import PendleAPIError
from app.ingestion.normalize import (
    NormalizationError,
    NormalizedSeries,
    SourceRef,
    morpho_market_key,
    normalize_morpho_history,
    normalize_pendle_history,
    pendle_market_key,
)
from app.models.evidence import DataMode
from app.models.market import TokenRef
from app.models.timeseries import TimeseriesMetric
from app.storage.parquet_store import (
    FLAG_GAP_BEFORE,
    FLAG_SNAPSHOT,
    Observation,
    ObservationError,
    ParquetStore,
    add_gap_flags,
    observations_from_series,
)
from app.storage.sqlite_store import SqliteStore

INTERVALS = {"day": (86400, "DAY"), "hour": (3600, "HOUR")}
PENDLE_SOURCE = "pendle_history"

@dataclass
class SourceResult:
    name: str
    status: str  # written | dry-run | unavailable | error
    reason: str | None = None


@dataclass
class SeriesReport:
    entity_id: str
    metric: str
    source_id: str
    written: int
    with_value: int
    missing: int
    gap_before: int
    first_ts: datetime | None
    last_ts: datetime | None
    dropped_future: int = 0
    dropped_off_grid: int = 0


@dataclass
class BackfillResult:
    sources: list[SourceResult] = field(default_factory=list)
    series: list[SeriesReport] = field(default_factory=list)

    def wrote(self, source: str) -> bool:
        return any(s.name == source and s.status in ("written", "dry-run") for s in self.sources)


def _store_series(
    normalized: dict[TimeseriesMetric, NormalizedSeries],
    entity_id: str,
    raw: Any,
    interval_s: int,
    parquet: ParquetStore,
    start: datetime,
    dry_run: bool,
) -> list[SeriesReport]:
    reports = []
    for metric, ns in normalized.items():
        rows = observations_from_series(ns.dto, entity_id, raw.retrieved_at)
        kept: list[Observation] = []
        future = off_grid = 0
        for r in rows:
            if r.ts_utc > r.retrieved_at:
                future += 1
            elif int(r.ts_utc.timestamp()) % interval_s:
                off_grid += 1
            else:
                kept.append(r)
        if dry_run:
            stored = add_gap_flags(kept, interval_s)
        else:
            parquet.write_observations(kept, DataMode.REAL, expected_interval_seconds=interval_s)
            # Stats over this run's window, starting at the first bucket actually returned.
            stored = [
                r for r in parquet.read_observations(
                    entity_id, metric.value, DataMode.REAL,
                    start=min([start, *(r.ts_utc for r in kept)]), source_id=ns.dto.source_id,
                )
                if FLAG_SNAPSHOT not in r.quality_flags  # refresh_current points are not history
            ]
        reports.append(SeriesReport(
            entity_id=entity_id,
            metric=metric.value,
            source_id=ns.dto.source_id,
            written=len(kept),
            with_value=sum(1 for r in stored if r.value_decimal is not None),
            missing=sum(1 for r in stored if r.value_decimal is None),
            gap_before=sum(1 for r in stored if FLAG_GAP_BEFORE in r.quality_flags),
            first_ts=stored[0].ts_utc if stored else None,
            last_ts=stored[-1].ts_utc if stored else None,
            dropped_future=future,
            dropped_off_grid=off_grid,
        ))
    return reports


def run_backfill(
    manifest: dict[str, Any],
    *,
    pendle: Any,
    morpho: Any,
    parquet: ParquetStore,
    sqlite: SqliteStore | None,
    loan_token: TokenRef,
    now: datetime,
    days: int,
    time_frame: str,
    dry_run: bool = False,
) -> BackfillResult:
    interval_s, morpho_interval = INTERVALS[time_frame]
    start = now - timedelta(days=days)
    pendle_address = manifest["pendleMarketAddress"]
    market_id = manifest["morphoMarketId"]
    start_ts, end_ts = int(start.timestamp()), int(now.timestamp())
    result = BackfillResult()

    def morpho_normalize(raw: Any) -> dict[TimeseriesMetric, NormalizedSeries]:
        return normalize_morpho_history(
            raw.payload, market_id, SourceRef.from_response(raw), time_frame=time_frame, loan_token=loan_token
        )

    sources: list[tuple[str, str, Callable[[], Any], Callable[[Any], dict]]] = [
        (
            PENDLE_SOURCE, pendle_market_key(pendle_address),
            lambda: pendle.fetch_market_history(pendle_address, time_frame, start, now),
            lambda raw: normalize_pendle_history(
                raw.payload, pendle_address, SourceRef.from_response(raw), time_frame=time_frame
            ),
        ),
        (
            "morpho_market_history", morpho_market_key(market_id),
            lambda: morpho.fetch_market_history(market_id, start_ts, end_ts, interval=morpho_interval),
            morpho_normalize,
        ),
        (
            "morpho_apy_history", morpho_market_key(market_id),
            lambda: morpho.fetch_apy_history(market_id, start_ts, end_ts, interval=morpho_interval),
            morpho_normalize,
        ),
    ]
    for name, entity_id, fetch, normalize in sources:
        try:
            raw = fetch()
            if sqlite is not None and not dry_run:
                sqlite.save_evidence([raw.to_evidence()])
            reports = _store_series(normalize(raw), entity_id, raw, interval_s, parquet, start, dry_run)
        except MorphoEndpointUnavailable as exc:
            result.sources.append(SourceResult(name, "unavailable", str(exc)))
            continue
        except (MorphoAPIError, PendleAPIError, NormalizationError, ObservationError, ValueError) as exc:
            result.sources.append(SourceResult(name, "error", f"{type(exc).__name__}: {exc}"[:500]))
            continue
        result.series.extend(reports)
        if not any(r.written for r in reports):
            result.sources.append(SourceResult(name, "error", "no point to write"))
        else:
            result.sources.append(SourceResult(name, "dry-run" if dry_run else "written"))
    return result
