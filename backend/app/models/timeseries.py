"""Data contract v1.0 — historical series of a single metric for one subject.

Missing points keep `value=None` with status MISSING/ERROR; they are never
filled with 0 or interpolated here. Exit prices are size-dependent and are
not represented as a timeseries.
"""
from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from .evidence import (
    BlockNumber,
    ContractModel,
    DataStatus,
    EvidenceId,
    PreciseDecimal,
    SourceId,
    UtcDatetime,
    VersionedModel,
    normalize_address,
    normalize_bytes32,
)
from .market import PricingMethod, TokenRef


class SeriesUnit(str, Enum):
    RATIO = "RATIO"  # 0.05 == 5 %
    USD = "USD"
    TOKEN = "TOKEN"  # whole units of `quote_asset`


class TimeseriesMetric(str, Enum):
    MARKET_PRICE = "MARKET_PRICE"
    ORACLE_PRICE = "ORACLE_PRICE"
    PT_IMPLIED_APY = "PT_IMPLIED_APY"
    UNDERLYING_APY = "UNDERLYING_APY"
    SUPPLY_APY = "SUPPLY_APY"
    BORROW_APY = "BORROW_APY"
    UTILIZATION = "UTILIZATION"
    TOTAL_SUPPLY_ASSETS = "TOTAL_SUPPLY_ASSETS"
    TOTAL_BORROW_ASSETS = "TOTAL_BORROW_ASSETS"
    LIQUIDITY_USD = "LIQUIDITY_USD"


class SubjectKind(str, Enum):
    ASSET = "ASSET"    # subject_id = token address
    MARKET = "MARKET"  # subject_id = Morpho bytes32 id or Pendle market address


_ALLOWED_UNITS: dict[TimeseriesMetric, frozenset[SeriesUnit]] = {
    TimeseriesMetric.MARKET_PRICE: frozenset({SeriesUnit.USD, SeriesUnit.TOKEN}),
    TimeseriesMetric.ORACLE_PRICE: frozenset({SeriesUnit.TOKEN}),
    TimeseriesMetric.PT_IMPLIED_APY: frozenset({SeriesUnit.RATIO}),
    TimeseriesMetric.UNDERLYING_APY: frozenset({SeriesUnit.RATIO}),
    TimeseriesMetric.SUPPLY_APY: frozenset({SeriesUnit.RATIO}),
    TimeseriesMetric.BORROW_APY: frozenset({SeriesUnit.RATIO}),
    TimeseriesMetric.UTILIZATION: frozenset({SeriesUnit.RATIO}),
    TimeseriesMetric.TOTAL_SUPPLY_ASSETS: frozenset({SeriesUnit.TOKEN}),
    TimeseriesMetric.TOTAL_BORROW_ASSETS: frozenset({SeriesUnit.TOKEN}),
    TimeseriesMetric.LIQUIDITY_USD: frozenset({SeriesUnit.USD}),
}

_ALLOWED_METHODS: dict[TimeseriesMetric, frozenset[PricingMethod]] = {
    TimeseriesMetric.MARKET_PRICE: frozenset({PricingMethod.API_SPOT, PricingMethod.PENDLE_IMPLIED}),
    TimeseriesMetric.ORACLE_PRICE: frozenset({PricingMethod.MORPHO_ORACLE}),
}

_ASSET_METRICS = frozenset({TimeseriesMetric.MARKET_PRICE})

_EMPTY_STATUSES = frozenset({DataStatus.MISSING, DataStatus.ERROR})


class TimeseriesPointDTO(ContractModel):
    timestamp: UtcDatetime
    value: PreciseDecimal | None = None
    status: DataStatus
    block_number: BlockNumber | None = None
    evidence_ids: tuple[EvidenceId, ...] = ()

    @model_validator(mode="after")
    def _consistency(self) -> "TimeseriesPointDTO":
        if self.value is None and self.status not in _EMPTY_STATUSES:
            raise ValueError("point without value must be MISSING or ERROR")
        if self.value is not None and self.status in _EMPTY_STATUSES:
            raise ValueError("point with value cannot be MISSING or ERROR")
        return self


class TimeseriesDTO(VersionedModel):
    series_id: str = Field(pattern=r"^ts_[0-9a-z_\-]{4,96}$")
    metric: TimeseriesMetric
    subject_kind: SubjectKind
    subject_id: str
    unit: SeriesUnit
    quote_asset: TokenRef | None = None
    pricing_method: PricingMethod | None = None
    interval_seconds: int | None = Field(default=None, gt=0)
    source_id: SourceId
    status: DataStatus
    points: tuple[TimeseriesPointDTO, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _normalize_subject(cls, data):
        if isinstance(data, dict) and isinstance(data.get("subject_id"), str):
            data = {**data, "subject_id": data["subject_id"].strip().lower()}
        return data

    @model_validator(mode="after")
    def _consistency(self) -> "TimeseriesDTO":
        if self.unit not in _ALLOWED_UNITS[self.metric]:
            raise ValueError(f"unit {self.unit.value} not allowed for {self.metric.value}")
        if (self.unit == SeriesUnit.TOKEN) != (self.quote_asset is not None):
            raise ValueError("quote_asset is required iff unit == TOKEN")
        if self.quote_asset is not None and self.quote_asset.chain_id != self.chain_id:
            raise ValueError("quote_asset chain_id differs from series chain_id")

        allowed_methods = _ALLOWED_METHODS.get(self.metric)
        if allowed_methods is None and self.pricing_method is not None:
            raise ValueError(f"pricing_method not applicable to {self.metric.value}")
        if allowed_methods is not None and self.pricing_method not in allowed_methods:
            raise ValueError(f"{self.metric.value} requires pricing_method in {sorted(m.value for m in allowed_methods)}")

        if self.subject_kind == SubjectKind.ASSET:
            if self.metric not in _ASSET_METRICS:
                raise ValueError(f"{self.metric.value} is a market metric")
            normalize_address(self.subject_id)
        else:
            if self.metric in _ASSET_METRICS:
                raise ValueError(f"{self.metric.value} is an asset metric")
            if len(self.subject_id) == 66:
                normalize_bytes32(self.subject_id)
            else:
                normalize_address(self.subject_id)

        stamps = [p.timestamp for p in self.points]
        if any(b <= a for a, b in zip(stamps, stamps[1:])):
            raise ValueError("points must have strictly increasing timestamps")

        point_statuses = {p.status for p in self.points}
        if self.status == DataStatus.OK and (not self.points or point_statuses != {DataStatus.OK}):
            raise ValueError("series status OK requires non-empty points, all OK")
        if self.status == DataStatus.PARTIAL and not (point_statuses - _EMPTY_STATUSES):
            raise ValueError("series status PARTIAL requires at least one point with a value")
        return self