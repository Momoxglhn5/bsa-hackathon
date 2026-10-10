"""Internal Monte Carlo PR1 contracts; no simulation calculations."""
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


class SimulationParams(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    model: Literal["GBM_ORACLE_PRICE_V1"]
    source_mode: SourceMode
    annual_volatility: PreciseDecimal = Field(ge=0, le=5)
    annual_drift: PreciseDecimal = Decimal("0")
    debt_model: DebtModel
    borrow_apr: PreciseDecimal | None = None

    @field_validator("source_mode")
    @classmethod
    def validate_source_mode(cls, value: SourceMode) -> SourceMode:
        if value == SourceMode.OBSERVED_ORACLE_SERIES:
            raise ValueError("OBSERVED_ORACLE_SERIES is planned for PR2")
        return value

    @model_validator(mode="after")
    def validate_debt_model(self) -> SimulationParams:
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
    loan_token_symbol: str
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
        if self.input_scope == InputScope.REAL_DATA:
            if not self.evidence_ids:
                raise ValueError("REAL_DATA requires non-empty evidence_ids")
            if self.simulation.source_mode != SourceMode.OBSERVED_ORACLE_SERIES:
                raise ValueError(
                    "REAL_DATA requires OBSERVED_ORACLE_SERIES; "
                    "OBSERVED_ORACLE_SERIES is planned for PR2"
                )
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

    @field_validator("calibration")
    @classmethod
    def validate_calibration(cls, value: SourceMode) -> SourceMode:
        if value == SourceMode.OBSERVED_ORACLE_SERIES:
            raise ValueError("OBSERVED_ORACLE_SERIES is planned for PR2")
        return value


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
