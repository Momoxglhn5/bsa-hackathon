from typing import Literal

from pydantic import Field, model_validator

from app.analytics.valuation import Valuation
from app.models.evidence import (ContractModel, EvidenceId, NonNegativeDecimal,
                                 PreciseDecimal, UnitRatio)

ScenarioType = Literal["ORACLE_MANIPULATION", "ORACLE_OUTAGE", "UNAUTHORIZED_MINTING", "CROSS_CHAIN_FAILURE"]


class ScenarioParameters(ContractModel):
    oracle_price_change_ratio: PreciseDecimal | None = None
    mint_amount: NonNegativeDecimal | None = Field(default=None, le=10**60)
    backing_impairment_ratio: UnitRatio | None = None
    assumed_token_haircut_ratio: UnitRatio | None = None
    haircut_applies_to_affected_collateral: bool | None = None
    outage_duration_hours: NonNegativeDecimal | None = Field(default=None, le=8760)
    unavailable_service: Literal["redemption", "bridge_transfer"] | None = None


class SimulationRequest(ContractModel):
    model_config = {"json_schema_extra": {"examples": [{
        "strategy_id": "strat_d589cd964a2da9b34f7a5ab5", "position_id": "pos_demo_fund",
        "scenario_type": "ORACLE_MANIPULATION", "target_node_id": "oracle:1:0x5555555555555555555555555555555555555555",
        "parameters": {"oracle_price_change_ratio": "-0.4"}, "horizon_days": 7, "scenario_scope": "HYPOTHETICAL",
    }]}}
    strategy_id: str
    position_id: str
    scenario_type: ScenarioType
    target_node_id: str
    parameters: ScenarioParameters = Field(default_factory=ScenarioParameters)
    horizon_days: int = Field(default=7, strict=True, ge=1, le=365)
    scenario_scope: Literal["VERIFIED_TARGET", "OBSERVED_TARGET", "HYPOTHETICAL"] = "HYPOTHETICAL"

    @model_validator(mode="after")
    def scenario_parameters(self):
        p = self.parameters
        allowed = {
            "ORACLE_MANIPULATION": {"oracle_price_change_ratio"},
            "ORACLE_OUTAGE": set(),
            "UNAUTHORIZED_MINTING": {"mint_amount", "backing_impairment_ratio", "assumed_token_haircut_ratio"},
            "CROSS_CHAIN_FAILURE": {"outage_duration_hours", "unavailable_service", "assumed_token_haircut_ratio", "haircut_applies_to_affected_collateral"},
        }[self.scenario_type]
        supplied = {k for k, v in p.model_dump().items() if v is not None}
        if supplied - allowed:
            raise ValueError("parameters are not applicable to this scenario")
        if self.scenario_type == "ORACLE_MANIPULATION":
            if p.oracle_price_change_ratio is None or not -1 <= p.oracle_price_change_ratio <= 100:
                raise ValueError("oracle_price_change_ratio must be between -1 and 100")
        return self


class PositionEffect(ContractModel):
    before: Valuation
    after: Valuation
    scenario_estimated_loss_usd: NonNegativeDecimal | None = None


class SimulationResult(ContractModel):
    simulation_id: str
    strategy_id: str
    position_id: str
    horizon_days: int
    scenario_type: ScenarioType
    scenario_scope: str
    scenario_status: Literal["CALCULATED", "PARTIAL", "NOT_APPLICABLE"]
    affected_node_ids: tuple[str, ...] = ()
    affected_position_ids: tuple[str, ...] = ()
    confirmed_exposed_capital_usd: NonNegativeDecimal | None = None
    potentially_exposed_capital_usd: NonNegativeDecimal | None = None
    gross_exposure_usd: NonNegativeDecimal | None = None
    operationally_blocked_usd: NonNegativeDecimal | None = None
    scenario_estimated_loss_usd: NonNegativeDecimal | None = None
    position_effects: dict[str, PositionEffect] = Field(default_factory=dict)
    representative_paths: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    assumptions: tuple[str, ...] = ()
    evidence_ids: tuple[EvidenceId, ...] = ()
    warnings: tuple[str, ...] = ()
