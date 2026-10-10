from decimal import Decimal, localcontext

from app.analytics.valuation import value_position
from app.decision.exit_cost import evaluate_exit
from app.decision.forecast import forecast_carry
from app.decision.schemas import DecisionRequest, DecisionResult


def decide(snapshot, request: DecisionRequest, simulations, now, risk_context=None) -> DecisionResult:
    position = snapshot.position(request.strategy_id, request.position_id)
    valuation = value_position(snapshot, position, now=now)
    quote_status, cost, external, quote_note = evaluate_exit(snapshot, position, request.exit_quote_id, now)
    warnings, included, excluded, unquantified, losses = [], [], [], [], {}
    incomplete = False
    for assumption in request.scenario_assumptions:
        result = simulations.get(assumption.loss_scenario_ref)
        if result is None:
            raise KeyError("Unknown simulation; run POST /simulations again")
        if (result.strategy_id != request.strategy_id or result.position_id != request.position_id
                or result.horizon_days != request.horizon_days):
            raise ValueError("simulation position, strategy or horizon does not match decision")
        effect = result.position_effects.get(request.position_id)
        loss = effect.scenario_estimated_loss_usd if effect else None
        if not assumption.include_in_financial_comparison:
            excluded.append(result.simulation_id)
            if loss is None:
                unquantified.append(result.simulation_id)
            warnings.append("UNQUANTIFIED_RISK_NOT_INCLUDED" if loss is None else "SCENARIO_EXPLICITLY_EXCLUDED")
            continue
        included.append(result.simulation_id)
        if loss is None or assumption.probability_over_horizon is None:
            incomplete = True
        else:
            losses[result.simulation_id] = (assumption.probability_over_horizon, loss)
    if len(included) > 1 and not request.scenarios_mutually_exclusive:
        warnings.append("OVERLAPPING_SCENARIOS_NOT_AGGREGATED")
        incomplete = True
    if not included:
        incomplete = True
        warnings.append("NO_SCENARIO_INCLUDED")
    expected = None if incomplete else sum((p*l for p, l in losses.values()), Decimal(0))
    equity, carry = valuation.equity_usd, request.estimated_net_carry_usd
    effective_mode, fallback, bundle_id = "normal", None, None
    if request.mode == "predictive":
        forecast, fallback, bundle_id = forecast_carry(snapshot, request, now)
        if fallback is None:
            bundle = next(b for b in snapshot.forecasts if b.bundle_id == bundle_id)
            mapped = {}
            if bundle.scenario_loss_basis == "FORECAST_NO_INCIDENT_VALUE":
                for ref, (probability, loss) in losses.items():
                    result = simulations[ref]
                    effect = result.position_effects[request.position_id]
                    before, after = effect.before, effect.after
                    # Only the explicit proportional collateral haircut has a known mapping.
                    if loss == 0:
                        mapped[ref] = (probability, loss)
                    elif (result.scenario_type in {"UNAUTHORIZED_MINTING", "CROSS_CHAIN_FAILURE"}
                          and any(a.startswith("assumed_token_haircut_ratio=") for a in result.assumptions)
                          and before.collateral_value_usd is not None and before.collateral_value_usd > 0
                          and after.collateral_value_usd is not None
                          and before.debt_value_usd == after.debt_value_usd
                          and loss == before.collateral_value_usd-after.collateral_value_usd):
                        with localcontext() as ctx:
                            ctx.prec = 60
                            mapped[ref] = (probability, loss / before.collateral_value_usd
                                          * bundle.projected_collateral_value_usd)
                    else:
                        fallback = "FORECAST_LOSS_MAPPING_UNAVAILABLE"
                        break
            if fallback is None:
                if bundle.scenario_loss_basis == "FORECAST_NO_INCIDENT_VALUE":
                    losses = mapped
                    expected = None if incomplete else sum((p*l for p, l in losses.values()), Decimal(0))
                    warnings.append("SCENARIO_HAIRCUT_APPLIED_TO_FORECAST_COLLATERAL")
                carry, effective_mode = forecast, "predictive"
                warnings.append("FORECAST_CARRY_CONDITIONAL_NOT_GUARANTEED")
            else:
                bundle_id = None
    stay = equity + carry - expected if all(v is not None for v in (equity, carry, expected)) else None
    exit_value = equity - cost if equity is not None and cost is not None else None
    status = quote_status if quote_status != "OK" else "CONDITIONAL"
    if quote_status == "OK" and (stay is None or exit_value is None):
        status = "INSUFFICIENT_DATA"
    comparison = verdict = threshold = threshold_status = probability_max = None
    sensitivity = request.sensitivity_scenario_ref or (included[0] if included else None)
    if status == "CONDITIONAL":
        comparison = "STAY_BETTER" if stay > exit_value else "EXIT_BETTER" if stay < exit_value else "EQUAL"
        verdict = "STAY" if comparison == "STAY_BETTER" else "EXIT" if comparison == "EXIT_BETTER" else None
        if sensitivity in losses:
            p, loss = losses[sensitivity]
            others = expected - p * loss
            probability_max = Decimal(1) - sum((v[0] for k, v in losses.items() if k != sensitivity), Decimal(0))
            if loss == 0:
                threshold_status = "NO_SENSITIVITY_ZERO_LOSS"
            else:
                with localcontext() as ctx:
                    ctx.prec = 60
                    threshold = (carry + cost - others) / loss
                threshold_status = "WITHIN_FEASIBLE_RANGE" if 0 <= threshold <= probability_max else "NO_REVERSAL_IN_FEASIBLE_RANGE"
    if carry is None:
        warnings.append("NET_CARRY_UNKNOWN")
    risk_note = ("Structural Risk Index is explanatory only and never used as a probability.",)
    if risk_context is not None and risk_context.risk_index is None:
        warnings.append("STRUCTURAL_RISK_INSUFFICIENT_DATA")
    return DecisionResult(requested_mode=request.mode, horizon_days=request.horizon_days,
                          effective_mode=effective_mode, fallback_reason=fallback, forecast_bundle_id=bundle_id,
                          decision_status=status, verdict=verdict, comparison=comparison,
                          equity_now_usd=equity, stay_value_usd=stay, exit_value_usd=exit_value,
                          expected_loss_usd=expected, exit_cost_usd=cost, net_carry_usd=carry,
                          break_even_probability=threshold, break_even_status=threshold_status,
                          sensitivity_scenario_ref=sensitivity, sensitivity_probability_max=probability_max,
                          quote_status=quote_status, requires_external_repayment_liquidity=external,
                          included_scenario_ids=tuple(included), excluded_scenario_ids=tuple(excluded),
                          excluded_unquantified_scenario_ids=tuple(unquantified),
                          structural_risk_index=risk_context.risk_index if risk_context else None,
                          risk_completeness_pct=risk_context.completeness_pct if risk_context else None,
                          explanation=("Conditional comparison in USD using the same current equity.",
                                       ("Probabilities are user assumptions; net carry comes from the identified validated forecast mapping." if bundle_id else "Probabilities and net carry are explicit user assumptions for this horizon."),
                                       "Selected incidents are alternative exclusive scenarios; the remaining probability has no selected incident.",
                                       quote_note,
                                       "At the break-even probability STAY and EXIT are equal; above it EXIT is preferred (other inputs fixed).") + risk_note,
                          warnings=tuple(sorted(set(warnings))))
