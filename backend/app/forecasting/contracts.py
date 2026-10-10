"""Forecast contract v1.0, initial scope: PT spot in USD, daily horizons."""
from typing import Literal

from pydantic import Field, model_validator

from ..models.evidence import ContractModel, NonNegativeDecimal, PositiveDecimal, UtcDatetime

ForecastStatus = Literal[
    "VALIDATED", "INSUFFICIENT_HISTORY", "NOT_BETTER_THAN_BASELINE",
    "MODEL_ERROR", "STALE", "NOT_RUN",
]


class ForecastPoint(ContractModel):
    horizon_days: Literal[1, 3, 7]
    q10: PositiveDecimal | None = None
    q50: PositiveDecimal | None = None
    q90: PositiveDecimal | None = None

    @model_validator(mode="after")
    def quantiles(self):
        vals = (self.q10, self.q50, self.q90)
        if any(v is not None for v in vals):
            if any(v is None for v in vals) or not self.q10 <= self.q50 <= self.q90:
                raise ValueError("quantiles must be complete and ordered")
        return self


class BacktestSummary(ContractModel):
    protocol: Literal["walk-forward"] = "walk-forward"
    n_evaluations: int = Field(default=0, ge=0)
    mae_model: NonNegativeDecimal | None = None
    mae_naive: NonNegativeDecimal | None = None
    rmse_model: NonNegativeDecimal | None = None
    coverage_q10_q90: NonNegativeDecimal | None = Field(default=None, le=1)
    validation_status: ForecastStatus = "NOT_RUN"


class ForecastBundle(ContractModel):
    schema_version: Literal["forecast-contract-v1.0"] = "forecast-contract-v1.0"
    forecast_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    chain_id: Literal[1] = 1
    entity_id: str = Field(pattern=r"^pendle:1:0x[0-9a-f]{40}$")
    target_metric: Literal["PT_MARKET_PRICE_USD"] = "PT_MARKET_PRICE_USD"
    unit: Literal["USD_PER_PT"] = "USD_PER_PT"
    frequency: Literal["1d"] = "1d"
    horizons_days: tuple[Literal[1], Literal[3], Literal[7]] = (1, 3, 7)
    model_id: Literal["amazon/chronos-2"] = "amazon/chronos-2"
    model_version: str = Field(min_length=1)
    trained_on_defi: Literal[False] = False
    zero_shot: Literal[True] = True
    status: ForecastStatus = "NOT_RUN"
    generated_at: UtcDatetime | None = None
    latest_observation_at: UtcDatetime | None = None
    training_cutoff_at: UtcDatetime | None = None
    source_snapshot_ref: str | None = Field(default=None, min_length=1)
    backtest: BacktestSummary = Field(default_factory=BacktestSummary)
    points: tuple[ForecastPoint, ...]
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def consistent(self):
        if tuple(p.horizon_days for p in self.points) != self.horizons_days:
            raise ValueError("expected exactly J+1/J+3/J+7 in order")
        if self.status == "VALIDATED":
            if not all((self.generated_at, self.latest_observation_at,
                        self.training_cutoff_at, self.source_snapshot_ref)):
                raise ValueError("validated forecast requires dates and snapshot")
            if not self.training_cutoff_at == self.latest_observation_at <= self.generated_at:
                raise ValueError("cutoff must equal latest observation and precede generation")
            b = self.backtest
            if (b.validation_status != "VALIDATED" or b.n_evaluations < 9
                    or b.mae_model is None or b.mae_naive is None
                    or b.rmse_model is None or b.coverage_q10_q90 is None
                    or b.mae_model >= b.mae_naive):
                raise ValueError("requires measured gain over baseline and >=9 horizon evaluations")
            if any(p.q50 is None for p in self.points):
                raise ValueError("validated forecast requires complete quantiles")
        return self
