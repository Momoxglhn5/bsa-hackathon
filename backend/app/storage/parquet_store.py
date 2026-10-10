"""Parquet store for timeseries observations (pyarrow only).

Layout: <data dir>/timeseries/<real|demo>/<metric>/<entity-slug>.parquet — one file per
(entity, metric), so different price concepts are physically separate and REAL never
shares a file with DEMO.

Values are exact Decimal text (`value_decimal`), null when missing — never 0. Missing
points are not filled and nothing is interpolated (`is_interpolated` is always False);
gaps are only flagged (GAP_BEFORE). No backfill orchestration, no risk logic.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable

import pyarrow as pa
import pyarrow.parquet as pq

from app.models.evidence import DataMode, DataStatus, decimal_to_str, normalize_address
from app.models.market import PricingMethod, TokenRef
from app.models.timeseries import (
    _ALLOWED_METHODS,
    _ALLOWED_UNITS,
    SeriesUnit,
    SubjectKind,
    TimeseriesDTO,
    TimeseriesMetric,
    TimeseriesPointDTO,
)
from app.storage.sqlite_store import default_data_dir

FLAG_MISSING = "MISSING"
FLAG_ERROR = "ERROR"
FLAG_GAP_BEFORE = "GAP_BEFORE"
FLAG_SNAPSHOT = "SNAPSHOT"  # intra-period "current state" point, off the history grid

_ENTITY_RE = re.compile(r"^(asset|morpho|pendle):(\d+):(0x[0-9a-f]{40}|0x[0-9a-f]{64})$")
_STATUS_FLAGS = {s.value for s in DataStatus} - {DataStatus.OK.value}

_UTC_US = pa.timestamp("us", tz="UTC")
SCHEMA = pa.schema([
    pa.field("entity_id", pa.string(), nullable=False),
    pa.field("metric", pa.string(), nullable=False),
    pa.field("ts_utc", _UTC_US, nullable=False),
    pa.field("value_decimal", pa.string()),
    pa.field("unit", pa.string(), nullable=False),
    pa.field("source_id", pa.string(), nullable=False),
    pa.field("retrieved_at", _UTC_US, nullable=False),
    pa.field("quality_flags", pa.list_(pa.string()), nullable=False),
    pa.field("is_interpolated", pa.bool_(), nullable=False),
    pa.field("block_number", pa.int64()),
    pa.field("pricing_method", pa.string()),
])


class ObservationError(ValueError):
    """Rows violate the storage rules (future leakage, unit, mixed file, naive time)."""


@dataclass(frozen=True)
class Observation:
    entity_id: str
    metric: str
    ts_utc: datetime
    value_decimal: Decimal | None
    unit: str
    source_id: str
    retrieved_at: datetime
    quality_flags: tuple[str, ...] = ()
    is_interpolated: bool = False
    block_number: int | None = None
    pricing_method: str | None = None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _entity_subject(entity_id: str) -> tuple[int, SubjectKind, str]:
    match = _ENTITY_RE.match(entity_id)
    if not match:
        raise ObservationError(f"invalid entity_id {entity_id!r}")
    kind = SubjectKind.ASSET if match.group(1) == "asset" else SubjectKind.MARKET
    return int(match.group(2)), kind, match.group(3)


def observations_from_series(dto: TimeseriesDTO, entity_id: str, retrieved_at: datetime) -> list[Observation]:
    """Rows from a validated series. Non-OK point statuses become quality flags."""
    chain_id, kind, subject = _entity_subject(entity_id)
    if (chain_id, kind, subject) != (dto.chain_id, dto.subject_kind, dto.subject_id):
        raise ObservationError(f"entity_id {entity_id} does not match series subject {dto.subject_id}")
    return [
        Observation(
            entity_id=entity_id,
            metric=dto.metric.value,
            ts_utc=p.timestamp,
            value_decimal=p.value,
            unit=dto.unit.value,
            source_id=dto.source_id,
            retrieved_at=retrieved_at,
            quality_flags=() if p.status == DataStatus.OK else (p.status.value,),
            block_number=p.block_number,
            pricing_method=dto.pricing_method.value if dto.pricing_method else None,
        )
        for p in dto.points
    ]


def add_gap_flags(rows: Iterable[Observation], expected_interval_seconds: int) -> list[Observation]:
    """Flag GAP_BEFORE on a point that follows a gap > interval. Never adds points.

    SNAPSHOT points are off the grid: they neither close nor receive a gap flag.
    """
    ordered = sorted(rows, key=lambda r: r.ts_utc)
    limit = timedelta(seconds=expected_interval_seconds)
    out: list[Observation] = []
    prev: Observation | None = None
    for row in ordered:
        flags = tuple(f for f in row.quality_flags if f != FLAG_GAP_BEFORE)
        if FLAG_SNAPSHOT not in flags:
            if prev is not None and row.ts_utc - prev.ts_utc > limit:
                flags += (FLAG_GAP_BEFORE,)
            prev = row
        out.append(replace(row, quality_flags=flags))
    return out


def validate_observations(rows: list[Observation]) -> None:
    """Raise on any rule violation. One call = one file: single entity and metric."""
    if not rows:
        return
    if len({(r.entity_id, r.metric) for r in rows}) != 1:
        raise ObservationError("one file never mixes entities or metrics")
    for r in rows:
        _entity_subject(r.entity_id)
        for name in ("ts_utc", "retrieved_at"):
            if getattr(r, name).tzinfo is None:
                raise ObservationError(f"{name} must be timezone-aware UTC")
        if r.ts_utc > r.retrieved_at:
            raise ObservationError(f"ts_utc {r.ts_utc} after retrieved_at {r.retrieved_at} (future leakage)")
        try:
            metric, unit = TimeseriesMetric(r.metric), SeriesUnit(r.unit)
        except ValueError as exc:
            raise ObservationError(str(exc)) from None
        if unit not in _ALLOWED_UNITS[metric]:
            raise ObservationError(f"unit {unit.value} not allowed for {metric.value}")
        allowed = _ALLOWED_METHODS.get(metric)
        method = PricingMethod(r.pricing_method) if r.pricing_method else None
        if (allowed is None and method is not None) or (allowed is not None and method not in allowed):
            raise ObservationError(f"pricing_method {r.pricing_method} not allowed for {metric.value}")
        if r.is_interpolated:
            raise ObservationError("interpolated points are not stored")
        empty_flag = bool({FLAG_MISSING, FLAG_ERROR} & set(r.quality_flags))
        if (r.value_decimal is None) != empty_flag:
            raise ObservationError(f"{r.ts_utc}: null value iff MISSING or ERROR flag")


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


def _to_table(rows: list[Observation]) -> pa.Table:
    return pa.Table.from_pylist([{
        "entity_id": r.entity_id,
        "metric": r.metric,
        "ts_utc": _utc(r.ts_utc),
        "value_decimal": decimal_to_str(r.value_decimal) if r.value_decimal is not None else None,
        "unit": r.unit,
        "source_id": r.source_id,
        "retrieved_at": _utc(r.retrieved_at),
        "quality_flags": list(r.quality_flags),
        "is_interpolated": r.is_interpolated,
        "block_number": r.block_number,
        "pricing_method": r.pricing_method,
    } for r in rows], schema=SCHEMA)


def _from_table(table: pa.Table) -> list[Observation]:
    return [
        Observation(
            entity_id=d["entity_id"],
            metric=d["metric"],
            ts_utc=_utc(d["ts_utc"]),
            value_decimal=Decimal(d["value_decimal"]) if d["value_decimal"] is not None else None,
            unit=d["unit"],
            source_id=d["source_id"],
            retrieved_at=_utc(d["retrieved_at"]),
            quality_flags=tuple(d["quality_flags"]),
            is_interpolated=d["is_interpolated"],
            block_number=d["block_number"],
            pricing_method=d["pricing_method"],
        )
        for d in table.to_pylist()
    ]


class ParquetStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_data_dir() / "timeseries"

    def path_for(self, entity_id: str, metric: str, data_mode: DataMode) -> Path:
        _entity_subject(entity_id)
        TimeseriesMetric(metric)
        return self.root / DataMode(data_mode).value.lower() / metric / f"{entity_id.replace(':', '_')}.parquet"

    def _load(self, path: Path) -> list[Observation]:
        return _from_table(pq.read_table(path, schema=SCHEMA)) if path.exists() else []

    def write_observations(
        self,
        rows: Iterable[Observation],
        data_mode: DataMode,
        expected_interval_seconds: int | None = None,
    ) -> Path | None:
        """Merge into the (entity, metric) file; dedupe keeps the latest retrieved_at.

        With `expected_interval_seconds`, GAP_BEFORE flags are recomputed on the merged
        series (a backfilled gap loses its flag).
        """
        new = list(rows)
        if not new:
            return None
        validate_observations(new)
        path = self.path_for(new[0].entity_id, new[0].metric, data_mode)

        merged: dict[tuple, Observation] = {}
        for r in [*self._load(path), *new]:
            key = (r.entity_id, r.metric, _utc(r.ts_utc), r.source_id)
            if key not in merged or r.retrieved_at >= merged[key].retrieved_at:
                merged[key] = r
        out = sorted(merged.values(), key=lambda r: (r.ts_utc, r.source_id))
        if expected_interval_seconds is not None:
            out = add_gap_flags(out, expected_interval_seconds)
        validate_observations(out)

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".parquet.tmp")
        pq.write_table(_to_table(out), tmp)
        os.replace(tmp, path)
        return path

    def read_observations(
        self,
        entity_id: str,
        metric: str,
        data_mode: DataMode,
        start: datetime | None = None,
        end: datetime | None = None,
        source_id: str | None = None,
    ) -> list[Observation]:
        """Rows of one (entity, metric) in [start, end], sorted, strictly increasing ts.

        A file may hold several sources (e.g. daily `morpho_api` history and `ethereum_rpc`
        snapshots); without `source_id` such a file raises instead of silently mixing them.
        """
        rows = [
            r for r in self._load(self.path_for(entity_id, metric, data_mode))
            if r.entity_id == entity_id and r.metric == metric
            and (source_id is None or r.source_id == source_id)
            and (start is None or r.ts_utc >= start)
            and (end is None or r.ts_utc <= end)
        ]
        if source_id is None and len(sources := {r.source_id for r in rows}) > 1:
            raise ObservationError(
                f"{entity_id} {metric}: several sources {sorted(sources)}; pass source_id"
            )
        rows.sort(key=lambda r: r.ts_utc)
        if any(b.ts_utc <= a.ts_utc for a, b in zip(rows, rows[1:])):
            raise ObservationError(f"{entity_id} {metric}: several rows per timestamp; pass source_id")
        return rows

    def read_series(
        self,
        entity_id: str,
        metric: str,
        data_mode: DataMode,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        source_id: str | None = None,
        quote_asset: TokenRef | None = None,
        interval_seconds: int | None = None,
    ) -> TimeseriesDTO | None:
        """Validated TimeseriesDTO, or None when no rows. `quote_asset` is required for unit TOKEN."""
        rows = self.read_observations(entity_id, metric, data_mode, start, end, source_id)
        if not rows:
            return None
        for name in ("unit", "source_id", "pricing_method"):
            if len({getattr(r, name) for r in rows}) != 1:
                raise ObservationError(f"{entity_id} {metric}: mixed {name} in one series")
        chain_id, kind, subject = _entity_subject(entity_id)
        points = tuple(
            TimeseriesPointDTO(
                timestamp=r.ts_utc,
                value=r.value_decimal,
                status=next((DataStatus(f) for f in r.quality_flags if f in _STATUS_FLAGS), DataStatus.OK),
                block_number=r.block_number,
            )
            for r in rows
        )
        statuses = {p.status for p in points}
        has_value = any(p.value is not None for p in points)
        status = (
            DataStatus.OK if statuses == {DataStatus.OK}
            else DataStatus.PARTIAL if has_value
            else DataStatus.MISSING
        )
        first = rows[0]
        slug = f"{metric}_{entity_id.replace(':', '_')}".lower()
        if len(slug) > 96:  # series_id limit; bytes32 market ids are long
            slug = f"{metric.lower()}_{hashlib.sha256(entity_id.encode()).hexdigest()[:24]}"
        return TimeseriesDTO(
            chain_id=chain_id,
            data_mode=data_mode,
            series_id=f"ts_{slug}",
            metric=TimeseriesMetric(metric),
            subject_kind=kind,
            subject_id=normalize_address(subject) if kind == SubjectKind.ASSET else subject,
            unit=SeriesUnit(first.unit),
            quote_asset=quote_asset,
            pricing_method=PricingMethod(first.pricing_method) if first.pricing_method else None,
            interval_seconds=interval_seconds,
            source_id=first.source_id,
            status=status,
            points=points,
        )
