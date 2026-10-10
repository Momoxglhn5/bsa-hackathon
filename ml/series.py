"""Adapt existing Data V1 DTOs without changing their public contract."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from backend.app.models.evidence import DataMode, DataStatus
from backend.app.models.strategy import StrategyDTO, StrategyStatus
from backend.app.models.timeseries import SeriesUnit, TimeseriesDTO, TimeseriesMetric


class SeriesUnavailable(ValueError):
    """The source cannot support this daily PT benchmark."""


@dataclass(frozen=True)
class DailySeries:
    strategy_id: str
    entity_id: str
    timestamps: tuple[datetime, ...]
    values: tuple[Decimal, ...]
    expiry: datetime
    source_snapshot_ref: str
    source_as_of: datetime
    retrieved_at: datetime
    evidence_ids: tuple[str, ...]
    data_mode: DataMode
    target_metric: str = "PT_MARKET_PRICE_USD"
    unit: str = "USD_PER_PT"


def prepare_pt_series(
    series: TimeseriesDTO,
    strategy: StrategyDTO,
    *,
    source_snapshot_ref: str,
    source_as_of: datetime,
    retrieved_at: datetime,
    min_observations: int = 45,
    exploratory: bool = False,
) -> DailySeries:
    """Reject gaps rather than invent/interpolate observations.

    Data V1 MARKET_PRICE is an ASSET series: match the exact PT address, not
    the Pendle market address from the illustrative brief's input example.
    DEMO is accepted for infrastructure tests and cannot validate a forecast.
    Collection timestamps describe this export, not historical availability;
    point-in-time backtest eligibility must be checked on the real dataset.
    """
    if min_observations < (14 if exploratory else 45):
        raise ValueError("minimum history cannot be below 45 daily observations")
    if not source_snapshot_ref.strip():
        raise SeriesUnavailable("MISSING_SNAPSHOT_REFERENCE")
    if any(t.tzinfo is None or t.utcoffset() is None for t in (source_as_of, retrieved_at)):
        raise SeriesUnavailable("UTC_AWARE_COLLECTION_TIMESTAMPS_REQUIRED")
    if source_as_of > retrieved_at:
        raise SeriesUnavailable("SOURCE_AS_OF_AFTER_RETRIEVAL")
    if series.data_mode != strategy.data_mode:
        raise SeriesUnavailable("DATA_MODE_MISMATCH")
    if series.data_mode == DataMode.REAL and strategy.status != StrategyStatus.VERIFIED_ACTIVE:
        raise SeriesUnavailable("STRATEGY_NOT_VERIFIED")
    if series.chain_id != 1 or strategy.chain_id != 1:
        raise SeriesUnavailable("WRONG_CHAIN")
    if series.metric != TimeseriesMetric.MARKET_PRICE or series.unit != SeriesUnit.USD:
        raise SeriesUnavailable("EXPECTED_PT_MARKET_PRICE_IN_USD")
    if series.subject_id != strategy.pt.address:
        raise SeriesUnavailable("WRONG_PT_ASSET")
    if not strategy.pendle_market_address or not strategy.expiry:
        raise SeriesUnavailable("MISSING_MARKET_OR_EXPIRY")
    if series.interval_seconds != 86400:
        raise SeriesUnavailable("DAILY_FREQUENCY_REQUIRED")
    if series.status != DataStatus.OK:
        raise SeriesUnavailable("SERIES_NOT_OK")
    if len(series.points) < min_observations:
        raise SeriesUnavailable("INSUFFICIENT_HISTORY")
    if any(p.status != DataStatus.OK or p.value is None or p.value <= 0 for p in series.points):
        raise SeriesUnavailable("INVALID_PRICE_OBSERVATIONS")
    if any(not p.evidence_ids for p in series.points):
        raise SeriesUnavailable("MISSING_POINT_EVIDENCE")
    stamps = tuple(p.timestamp for p in series.points)
    if any(b - a != timedelta(days=1) for a, b in zip(stamps, stamps[1:])):
        raise SeriesUnavailable("IRREGULAR_OR_MISSING_DAYS")
    if stamps[-1] > source_as_of:
        raise SeriesUnavailable("OBSERVATION_AFTER_SOURCE_AS_OF")
    if stamps[-1] + timedelta(days=7) >= strategy.expiry:
        raise SeriesUnavailable("HORIZON_REACHES_PT_EXPIRY")
    return DailySeries(
        strategy_id=strategy.strategy_id,
        entity_id=f"pendle:1:{strategy.pendle_market_address}",
        timestamps=stamps,
        values=tuple(p.value for p in series.points),
        expiry=strategy.expiry,
        source_snapshot_ref=source_snapshot_ref,
        source_as_of=source_as_of,
        retrieved_at=retrieved_at,
        evidence_ids=tuple(sorted({e for p in series.points for e in p.evidence_ids})),
        data_mode=series.data_mode,
    )
