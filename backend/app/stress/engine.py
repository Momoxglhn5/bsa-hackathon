import hashlib
from decimal import Decimal

from app.analytics.valuation import value_position
from app.graph.builder import build_graph
from app.graph.exposure import exposure
from app.graph.propagation import propagate
from app.stress.schemas import PositionEffect, SimulationRequest, SimulationResult


def simulate(snapshot, request: SimulationRequest, *, now=None) -> SimulationResult:
    now = now or snapshot.as_of
    snapshot.position(request.strategy_id, request.position_id)
    graph = build_graph(snapshot, request.strategy_id, hypothetical=request.scenario_scope == "HYPOTHETICAL")
    sid = "stress:" + hashlib.sha256((snapshot.model_dump_json() + request.model_dump_json()).encode()).hexdigest()[:24]
    base = dict(simulation_id=sid, strategy_id=request.strategy_id, position_id=request.position_id,
                horizon_days=request.horizon_days, scenario_type=request.scenario_type,
                scenario_scope=request.scenario_scope)
    target = next((n for n in graph.nodes if n.id == request.target_node_id), None)
    incoming = [e for e in graph.edges if e.target == request.target_node_id]
    bridge_verified = target is not None and target.type == "BRIDGE" and any(e.verification_status == "CONFIRMED" for e in incoming)
    if request.scenario_type == "CROSS_CHAIN_FAILURE" and (target is None or (request.scenario_scope != "HYPOTHETICAL" and not bridge_verified)):
        return SimulationResult(**base, scenario_status="NOT_APPLICABLE", warnings=("NO_VERIFIED_BRIDGE_DEPENDENCY",))
    if target is None:
        raise KeyError("Unknown target node")
    expected_types = {"ORACLE_MANIPULATION": {"ORACLE"}, "ORACLE_OUTAGE": {"ORACLE"},
                      "UNAUTHORIZED_MINTING": {"ASSET", "SY", "PROTOCOL", "CONTRACT"},
                      "CROSS_CHAIN_FAILURE": {"BRIDGE"}}
    if target.type not in expected_types[request.scenario_type]:
        raise ValueError("target node type does not match scenario")
    if request.scenario_scope == "VERIFIED_TARGET" and not any(e.verification_status == "CONFIRMED" for e in incoming):
        raise ValueError("VERIFIED_TARGET requires a confirmed dependency")
    if request.scenario_scope == "OBSERVED_TARGET" and not any(e.verification_status in {"CONFIRMED", "OBSERVED"} for e in incoming):
        raise ValueError("OBSERVED_TARGET requires observed or confirmed evidence")
    mode = {"ORACLE_MANIPULATION": "VALUATION", "ORACLE_OUTAGE": "LIVENESS",
            "UNAUTHORIZED_MINTING": "ECONOMIC", "CROSS_CHAIN_FAILURE": "LIVENESS"}[request.scenario_type]
    impact = propagate(snapshot, graph, [target.id], mode, now=now)
    ids = sorted(set(impact.confirmed_position_ids) | set(impact.potential_position_ids))
    effects, warnings = {}, []
    for pid in ids:
        p = snapshot.position(request.strategy_id, pid)
        before = value_position(snapshot, p, now=now)
        after, loss = before, None
        if request.scenario_type == "ORACLE_MANIPULATION":
            own_oracle = f"oracle:{p.chain_id}:{snapshot.market(p).morpho.oracle_address}"
            if target.id == own_oracle:
                after = value_position(snapshot, p, request.parameters.oracle_price_change_ratio, now=now)
            warnings.append("ORACLE_SHOCK_IS_NOT_A_SPOT_PRICE_LOSS; liquidation execution loss is unquantified")
        elif request.scenario_type == "UNAUTHORIZED_MINTING":
            haircut = request.parameters.assumed_token_haircut_ratio
            if haircut is not None and target.id == p.collateral_asset.asset_id and before.collateral_value_usd is not None:
                loss = before.collateral_value_usd * haircut
                cv = before.collateral_value_usd - loss
                dv = before.debt_value_usd
                # Economic haircut never rewrites the oracle health factor.
                after = before.model_copy(update={"collateral_value_usd": cv,
                                                   "equity_usd": cv-dv if dv is not None else None,
                                                   "gross_exposure_usd": cv+dv if dv is not None else None})
            elif request.parameters.backing_impairment_ratio is not None:
                warnings.append("BACKING_TO_TOKEN_PRICE_MAPPING_UNKNOWN")
            elif haircut is not None:
                warnings.append("HAIRCUT_IGNORED_TARGET_NOT_COLLATERAL; propagation of price loss is unquantified")
        elif request.scenario_type == "CROSS_CHAIN_FAILURE":
            haircut = request.parameters.assumed_token_haircut_ratio
            if haircut is not None and request.parameters.haircut_applies_to_affected_collateral is True and before.collateral_value_usd is not None:
                loss = before.collateral_value_usd * haircut
                cv, dv = before.collateral_value_usd - loss, before.debt_value_usd
                after = before.model_copy(update={"collateral_value_usd": cv,
                    "equity_usd": cv-dv if dv is not None else None,
                    "gross_exposure_usd": cv+dv if dv is not None else None})
                warnings.append("USER_ASSUMED_COLLATERAL_HAIRCUT: not inferred from bridge backing or outage duration")
            else:
                warnings.append("BRIDGE_RECOVERY_AND_ECONOMIC_BACKING_UNKNOWN; no automatic haircut transmission")
        effects[pid] = PositionEffect(before=before, after=after, scenario_estimated_loss_usd=loss)
    losses = [e.scenario_estimated_loss_usd for e in effects.values()]
    total_loss = sum(losses, Decimal(0)) if losses and all(x is not None for x in losses) else None
    if total_loss is None:
        warnings.append("CONDITIONAL_LOSS_UNQUANTIFIED")
    assumptions = ["Simulated failure; not evidence of an actual incident"]
    assumptions += [f"{k}={v}" for k, v in request.parameters.model_dump(mode="json").items() if v is not None]
    return SimulationResult(**base, scenario_status="CALCULATED" if total_loss is not None else "PARTIAL",
                            affected_node_ids=impact.affected_node_ids, affected_position_ids=tuple(ids),
                            confirmed_exposed_capital_usd=exposure(snapshot, impact.confirmed_position_ids, now=now),
                            potentially_exposed_capital_usd=exposure(snapshot, impact.potential_position_ids, now=now),
                            gross_exposure_usd=exposure(snapshot, ids, "gross_exposure_usd", now=now),
                            operationally_blocked_usd=exposure(snapshot, ids, now=now) if mode == "LIVENESS" else None,
                            scenario_estimated_loss_usd=total_loss, position_effects=effects,
                            representative_paths=impact.representative_paths,
                            assumptions=tuple(assumptions), evidence_ids=impact.evidence_ids,
                            warnings=tuple(sorted(set(warnings))))
