from typing import Literal

from pydantic import Field, model_validator

from app.models.evidence import ContractModel, NonNegativeDecimal, PreciseDecimal, UnitRatio
from app.decision.carry import CarryAssumptions, CarryResult
from app.decision.closing import ClosingAssumptions, ClosingPlanResult


class ScenarioAssumption(ContractModel):
    loss_scenario_ref: str
    probability_over_horizon: UnitRatio | None = None
    basis: Literal["USER_ASSUMPTION"] = "USER_ASSUMPTION"
    include_in_financial_comparison: bool = True


class DecisionRequest(ContractModel):
    model_config = {"json_schema_extra": {"examples": [{
        "strategy_id": "strat_d589cd964a2da9b34f7a5ab5", "position_id": "pos_demo_fund",
        "mode": "normal", "horizon_days": 7,
        "scenario_assumptions": [{"loss_scenario_ref": "REPLACE_WITH_RETURNED_SIMULATION_ID",
                                  "probability_over_horizon": "0.02", "basis": "USER_ASSUMPTION"}],
        "exit_quote_id": "quote:synthetic-full-position", "estimated_net_carry_usd": "1000",
        "carry_explanation": "Synthetic net carry over seven days",
    }]}}
    strategy_id: str
    position_id: str
    mode: Literal["normal", "predictive"] = "normal"
    horizon_days: int = Field(default=7, strict=True, ge=1, le=365)
    scenario_assumptions: tuple[ScenarioAssumption, ...] = Field(min_length=1, max_length=20)
    scenarios_mutually_exclusive: bool = False
    exit_quote_id: str | None = None
    estimated_net_carry_usd: PreciseDecimal | None = Field(default=None, ge=-10**18, le=10**18)
    carry_basis: Literal["USER_ASSUMPTION"] = "USER_ASSUMPTION"
    carry_explanation: str | None = Field(default=None, min_length=1, max_length=1000)
    sensitivity_scenario_ref: str | None = None
    carry_assumptions: CarryAssumptions | None = None
    closing_assumptions: ClosingAssumptions | None = None

    @model_validator(mode="after")
    def consistency(self):
        if self.estimated_net_carry_usd is not None and self.carry_assumptions is not None:
            raise ValueError("choose either explicit net carry or rate assumptions")
        ids = [s.loss_scenario_ref for s in self.scenario_assumptions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate scenario reference")
        if self.sensitivity_scenario_ref is not None and self.sensitivity_scenario_ref not in ids:
            raise ValueError("sensitivity scenario must be selected")
        if self.estimated_net_carry_usd is not None and not self.carry_explanation:
            raise ValueError("carry requires an explicit explanation for this horizon")
        included = [s for s in self.scenario_assumptions if s.include_in_financial_comparison]
        known = [s.probability_over_horizon for s in included if s.probability_over_horizon is not None]
        if sum(known) > 1 and self.scenarios_mutually_exclusive:
            raise ValueError("mutually exclusive scenario probabilities must sum to <= 1")
        return self


class DecisionResult(ContractModel):
    horizon_days: int
    requested_mode: str
    effective_mode: Literal["normal", "predictive"] = "normal"
    forecast_bundle_id: str | None = None
    fallback_reason: str | None = None
    decision_status: Literal["CONDITIONAL", "INSUFFICIENT_DATA", "EXIT_NOT_FEASIBLE", "QUOTE_UNAVAILABLE"]
    verdict: Literal["STAY", "EXIT"] | None = None
    comparison: Literal["STAY_BETTER", "EXIT_BETTER", "EQUAL"] | None = None
    equity_now_usd: PreciseDecimal | None = None
    stay_value_usd: PreciseDecimal | None = None
    exit_value_usd: PreciseDecimal | None = None
    expected_loss_usd: NonNegativeDecimal | None = None
    exit_cost_usd: PreciseDecimal | None = None
    net_carry_usd: PreciseDecimal | None = None
    break_even_probability: PreciseDecimal | None = None
    break_even_status: str | None = None
    sensitivity_scenario_ref: str | None = None
    sensitivity_probability_max: UnitRatio | None = None
    quote_status: str
    requires_external_repayment_liquidity: bool | None = None
    included_scenario_ids: tuple[str, ...] = ()
    excluded_scenario_ids: tuple[str, ...] = ()
    excluded_unquantified_scenario_ids: tuple[str, ...] = ()
    assumption_basis: Literal["ASSUMPTION_DRIVEN"] = "ASSUMPTION_DRIVEN"
    structural_risk_index: PreciseDecimal | None = None
    risk_completeness_pct: PreciseDecimal | None = None
    carry_calculation: CarryResult | None = None
    closing_plan: ClosingPlanResult | None = None
    explanation: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
