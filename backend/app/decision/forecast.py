"""Optional Forecast Cache contract, with an explicit horizon-specific mapping.

Forecast producers own validation/backtesting. No model runs in the API. Only an
explicit net-carry projection is accepted; a PT price never becomes an oracle.
"""
from typing import Literal
from pydantic import Field, model_validator
from app.models.evidence import ContractModel, EvidenceId, PreciseDecimal, UtcDatetime


class ForecastBundle(ContractModel):
    bundle_id: str
    strategy_id: str
    position_id: str
    horizon_days: int = Field(strict=True, ge=1, le=365)
    generated_at: UtcDatetime
    valid_until: UtcDatetime
    validation_status: Literal["VALIDATED", "NOT_VALIDATED"]
    transformation: Literal["NET_CARRY_USD_OVER_HORIZON"]
    estimated_net_carry_usd: PreciseDecimal = Field(ge=-10**18, le=10**18)
    methodology: str = Field(min_length=1)
    backtest_reference: str = Field(min_length=1)
    evidence_ids: tuple[EvidenceId, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def dates(self):
        if self.valid_until <= self.generated_at:
            raise ValueError("forecast validity must follow generation")
        return self


def forecast_carry(snapshot, request, now):
    bundles = [b for b in snapshot.forecasts if b.strategy_id == request.strategy_id
               and b.position_id == request.position_id and b.horizon_days == request.horizon_days]
    if not bundles:
        return None, "FORECAST_UNAVAILABLE", None
    bundle = max(bundles, key=lambda b: b.generated_at)
    if bundle.generated_at > now or bundle.valid_until < now:
        return None, "STALE", None
    if bundle.validation_status != "VALIDATED":
        return None, "NOT_VALIDATED", None
    evidence = {e.evidence_id: e for e in snapshot.evidence}
    if any(i not in evidence or evidence[i].status in {"MISSING", "ERROR", "MISMATCH", "STALE"}
           or evidence[i].fetched_at > now for i in bundle.evidence_ids):
        return None, "NOT_VALIDATED", None
    return bundle.estimated_net_carry_usd, None, bundle.bundle_id
