"""Internal Monte Carlo contracts (PR1 hypothetical, PR2 oracle calibration); no calculations."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer,
    field_validator, model_validator,
)

MODEL_VERSION = "GBM_ORACLE_PRICE_V1"
SCHEMA_VERSION = "monte-carlo-internal-v1"


class PositionSide(str, Enum):
    BORROW = "BORROW"
    SUPPLY = "SUPPLY"


class InputScope(str, Enum):
    HYPOTHETICAL = "HYPOTHETICAL"
    REAL_DATA = "REAL_DATA"


class SourceMode(str, Enum):
    USER_ASSUMPTION = "USER_ASSUMPTION"
    OBSERVED_ORACLE_SERIES = "OBSERVED_ORACLE_SERIES"


class DebtModel(str, Enum):
    FROZEN_DEBT = "FROZEN_DEBT"
    CONSTANT_APR = "CONSTANT_APR"


class McStatus(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


def _parse_decimal(value: Any) -> Decimal:
    """Accept finite decimals, numeric strings and integers, not floats/bools."""
    if isinstance(value, (float, bool)):
        raise ValueError("Use Decimal, a numeric string or an integer, not float/bool")
    if not isinstance(value, (Decimal, str, int)):
        raise ValueError("Expected Decimal, a numeric string or an integer")
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid decimal value") from exc
    if not result.is_finite():
        raise ValueError("NaN, Infinity and -Infinity are not allowed")
    return result


def _serialize_decimal(value: Decimal) -> str:
    return format(value, "f")


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Datetime must be timezone-aware")
    return value


PreciseDecimal = Annotated[
    Decimal,
    BeforeValidator(_parse_decimal),
    PlainSerializer(_serialize_decimal, return_type=str, when_used="json"),
]


CALIBRATION_METHOD = "ORACLE_DAILY_LOG_RETURNS_30D_V1"
CALIBRATION_RETURNS = 30
CALIBRATION_SAMPLING_SECONDS = 86400
CALIBRATION_TOLERANCE_SECONDS = 3600
CALIBRATION_ANNUALIZATION_DAYS = 365


class CalibrationPoint(BaseModel):
    """One sampled Morpho oracle observation and the RPC read proving it."""
    model_config = ConfigDict(extra="forbid", validate_default=True)
    target_time: datetime
    observed_at: datetime
    block_number: int = Field(strict=True, ge=0)
    oracle_price_loan_per_pt: PreciseDecimal = Field(gt=0)
    evidence_id: str

    @field_validator("target_time", "observed_at")
    @classmethod
    def aware_times(cls, value: datetime) -> datetime:
        return _require_timezone(value)

    @model_validator(mode="after")
    def observed_within_tolerance(self) -> CalibrationPoint:
        lag = (self.target_time - self.observed_at).total_seconds()
        if not 0 <= lag <= CALIBRATION_TOLERANCE_SECONDS:
            raise ValueError("Sample must be observed at most one tolerance before its target time")
        return self


class OracleCalibration(BaseModel):
    """Auditable PR2 calibration from one Morpho oracle series.

    31 daily samples give 30 log returns; volatility is annualized over 365 days.
    Drift is a fixed policy (zero), never estimated from 30 noisy returns.
    """
    model_config = ConfigDict(extra="forbid", validate_default=True)
    method: Literal["ORACLE_DAILY_LOG_RETURNS_30D_V1"] = CALIBRATION_METHOD
    series_id: str
    source_id: Literal["ethereum_rpc"] = "ethereum_rpc"
    chain_id: Literal[1] = 1
    market_id: str
    oracle_address: str
    collateral_token_address: str
    loan_token_address: str
    sampling_interval_seconds: Literal[86400] = CALIBRATION_SAMPLING_SECONDS
    sample_tolerance_seconds: Literal[3600] = CALIBRATION_TOLERANCE_SECONDS
    annualization_days: Literal[365] = CALIBRATION_ANNUALIZATION_DAYS
    window_start: datetime
    window_end: datetime
    observation_count: Literal[31] = CALIBRATION_RETURNS + 1
    return_count: Literal[30] = CALIBRATION_RETURNS
    daily_log_return_mean: PreciseDecimal
    daily_log_return_stdev: PreciseDecimal = Field(ge=0)
    annual_volatility: PreciseDecimal = Field(ge=0, le=5)
    annual_drift: PreciseDecimal = Decimal("0")
    drift_policy: Literal["FIXED_ZERO_NOT_ESTIMATED"] = "FIXED_ZERO_NOT_ESTIMATED"
    skipped_candidate_points: int = Field(default=0, strict=True, ge=0)
    ignored_future_points: int = Field(default=0, strict=True, ge=0)
    points: list[CalibrationPoint] = Field(min_length=31, max_length=31)
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("window_start", "window_end")
    @classmethod
    def aware_window(cls, value):
        return _require_timezone(value)

    @model_validator(mode="after")
    def ordered_window(self):
        if self.annual_drift != 0:
            raise ValueError("Calibration drift policy is fixed at zero")
        span = (self.window_end - self.window_start).total_seconds()
        if span != CALIBRATION_RETURNS * CALIBRATION_SAMPLING_SECONDS:
            raise ValueError("Calibration window must span exactly 30 sampling intervals")
        targets = [p.target_time for p in self.points]
        if targets[0] != self.window_start or targets[-1] != self.window_end or any(
            (b - a).total_seconds() != CALIBRATION_SAMPLING_SECONDS for a, b in zip(targets, targets[1:])
        ):
            raise ValueError("Calibration points must sit on the daily sampling grid")
        blocks = [p.block_number for p in self.points]
        if any(b <= a for a, b in zip(blocks, blocks[1:])):
            raise ValueError("Calibration blocks must be strictly increasing")
        if sorted(self.evidence_ids) != sorted({p.evidence_id for p in self.points}):
            raise ValueError("Calibration evidence_ids must be exactly the sampled points' proofs")
        return self


class SimulationParams(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    model: Literal["GBM_ORACLE_PRICE_V1"]
    source_mode: SourceMode
    annual_volatility: PreciseDecimal = Field(ge=0, le=5)
    annual_drift: PreciseDecimal = Decimal("0")
    debt_model: DebtModel
    borrow_apr: PreciseDecimal | None = None

    calibration: OracleCalibration | None = None

    @model_validator(mode="after")
    def validate_debt_model(self) -> SimulationParams:
        if self.source_mode == SourceMode.OBSERVED_ORACLE_SERIES:
            if self.calibration is None:
                raise ValueError("PR2 requires verified oracle calibration for OBSERVED_ORACLE_SERIES")
            if (self.annual_volatility != self.calibration.annual_volatility
                    or self.annual_drift != self.calibration.annual_drift):
                raise ValueError("Simulation parameters must match oracle calibration")
        elif self.calibration is not None:
            raise ValueError("USER_ASSUMPTION cannot carry observed calibration")
        if self.debt_model == DebtModel.CONSTANT_APR and self.borrow_apr is None:
            raise ValueError("borrow_apr is required for CONSTANT_APR")
        return self


class MonteCarloRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    strategy_id: str
    position_id: str
    position_side: PositionSide = PositionSide.BORROW
    snapshot_as_of: datetime
    input_scope: InputScope
    # Display label only; the address is the identity. Data may not know the symbol.
    loan_token_symbol: str | None = None
    loan_token_address: str | None = None
    collateral_quantity_pt: PreciseDecimal = Field(gt=0)
    initial_oracle_price_loan_per_pt: PreciseDecimal = Field(gt=0)
    debt_loan_units: PreciseDecimal = Field(ge=0)
    lltv: PreciseDecimal = Field(gt=0, le=1)
    horizon_days: Literal[1, 3, 7]
    num_paths: int = Field(strict=True, ge=1000, le=50000)
    seed: int = Field(strict=True)
    pt_expiry: datetime | None = None
    simulation: SimulationParams
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("horizon_days", mode="before")
    @classmethod
    def validate_horizon_type(cls, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("horizon_days must be an integer: 1, 3 or 7")
        return value

    @field_validator("snapshot_as_of")
    @classmethod
    def validate_snapshot_timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value)

    @field_validator("pt_expiry")
    @classmethod
    def validate_expiry_timezone(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_timezone(value)

    @model_validator(mode="after")
    def validate_real_data_requirements(self) -> MonteCarloRequest:
        if not self.loan_token_symbol and not self.loan_token_address:
            raise ValueError("loan token requires a symbol or an address")
        if self.simulation.calibration is not None:
            c = self.simulation.calibration
            if c.window_end > self.snapshot_as_of:
                raise ValueError("Calibration cannot use future observations")
            if not set(c.evidence_ids).issubset(self.evidence_ids):
                raise ValueError("Request must include calibration evidence_ids")
        if self.input_scope == InputScope.REAL_DATA:
            if not self.evidence_ids:
                raise ValueError("REAL_DATA requires non-empty evidence_ids")
            if self.simulation.source_mode != SourceMode.OBSERVED_ORACLE_SERIES:
                raise ValueError(
                    "REAL_DATA requires OBSERVED_ORACLE_SERIES; "
                    "PR2 requires observed oracle calibration"
                )
            if not self.loan_token_address:
                raise ValueError("REAL_DATA requires loan_token_address")
        return self


class MonteCarloSamplingCI(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    low: PreciseDecimal = Field(ge=0, le=1)
    high: PreciseDecimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_interval(self) -> MonteCarloSamplingCI:
        if self.low > self.high:
            raise ValueError("low must be less than or equal to high")
        return self


class MonteCarloData(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    strategy_id: str
    position_id: str
    model_version: str
    horizon_days: int
    initial_ltv: PreciseDecimal | None = None
    initial_health_factor: PreciseDecimal | None = None
    critical_oracle_drop_ratio: PreciseDecimal | None = None
    breached_paths: int | None = Field(default=None, strict=True, ge=0)
    total_paths: int | None = Field(default=None, strict=True, ge=0)
    simulated_threshold_breach_frequency: PreciseDecimal | None = Field(
        default=None, ge=0, le=1
    )
    terminal_ltv_p50: PreciseDecimal | None = None
    terminal_ltv_p95: PreciseDecimal | None = None
    min_health_factor_p05: PreciseDecimal | None = None
    mc_sampling_ci_95: MonteCarloSamplingCI | None = None
    oracle_mark_to_model_var95_loan_units: PreciseDecimal | None = None
    oracle_mark_to_model_es95_loan_units: PreciseDecimal | None = None
    realized_liquidation_loss_usd: PreciseDecimal | None = None
    assumptions: list[str] = Field(default_factory=list)

    @field_validator("realized_liquidation_loss_usd", mode="before")
    @classmethod
    def reject_realized_loss(cls, value: Any) -> None:
        if value is not None:
            raise ValueError("realized_liquidation_loss_usd must remain None in PR1")
        return None

    @model_validator(mode="after")
    def validate_path_counts(self) -> MonteCarloData:
        if (
            self.breached_paths is not None
            and self.total_paths is not None
            and self.breached_paths > self.total_paths
        ):
            raise ValueError("breached_paths cannot exceed total_paths")
        return self


class MonteCarloProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    calibration: SourceMode
    evidence_ids: list[str]

    oracle_calibration: OracleCalibration | None = None


class MonteCarloResult(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    schema_version: str = SCHEMA_VERSION
    status: McStatus
    as_of: datetime
    data: MonteCarloData | None
    provenance: MonteCarloProvenance
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("as_of")
    @classmethod
    def validate_as_of_timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value)
