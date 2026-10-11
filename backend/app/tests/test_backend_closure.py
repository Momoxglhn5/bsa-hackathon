"""Final backend guards; fixtures are synthetic, including REAL-shaped cases."""
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.analytics.dataset import AnalyticsSnapshot, load_snapshot
from app.analytics.service import AnalyticsService
from app.analytics.valuation import value_position
from app.decision.engine import decide
from app.decision.forecast import ForecastBundle, forecast_carry
from app.decision.exit_cost import evaluate_exit
from app.decision.carry import CarryAssumptions, estimate_carry
from app.main import DEFAULT_SNAPSHOT, create_app
from app.stress.schemas import SimulationResult
from app.tests.test_core_analytics import request, decision_input


def real_fixture():
    """Synthetic values labelled REAL solely to test mode guards, never live data."""
    payload = json.dumps(load_snapshot(DEFAULT_SNAPSHOT).model_dump(mode="json"))
    payload = payload.replace('"data_mode": "DEMO"', '"data_mode": "REAL"')
    payload = payload.replace('"source_kind": "FIXTURE"', '"source_kind": "DERIVED"')
    return AnalyticsSnapshot.model_validate_json(payload)


def forecast(snapshot, **changes):
    position = snapshot.positions[0]
    data = dict(bundle_id="test:closure-forecast", strategy_id=position.strategy_id,
        position_id=position.position_id, horizon_days=7, generated_at=snapshot.as_of,
        valid_until=snapshot.as_of+timedelta(minutes=5), validation_status="VALIDATED",
        transformation="NET_CARRY_USD_OVER_HORIZON", estimated_net_carry_usd="2000",
        methodology="Synthetic projection for expiry and isolation regression tests",
        backtest_reference="synthetic:test", evidence_ids=(snapshot.evidence[0].evidence_id,))
    data.update(changes)
    return ForecastBundle(**data)


def test_hero_path_graph_stress_normal_decision_and_probability_reversal():
    snapshot = load_snapshot(DEFAULT_SNAPSHOT)
    position = snapshot.positions[0]
    with TestClient(create_app(snapshot=snapshot, replay=True)) as api:
        graph = api.get(f"/api/v1/graph/{position.strategy_id}")
        assert graph.status_code == 200
        assert position.collateral_asset.asset_id in {n["id"] for n in graph.json()["data"]["nodes"]}
        shock = api.post("/api/v1/simulations", json={
            "strategy_id": position.strategy_id, "position_id": position.position_id,
            "scenario_type": "UNAUTHORIZED_MINTING", "scenario_scope": "HYPOTHETICAL",
            "target_node_id": position.collateral_asset.asset_id, "horizon_days": 7,
            "parameters": {"assumed_token_haircut_ratio": "0.2"},
        })
        assert shock.status_code == 200
        simulation = shock.json()["data"]
        assert Decimal(simulation["scenario_estimated_loss_usd"]) == 400000
        body = {
            "strategy_id": position.strategy_id, "position_id": position.position_id,
            "mode": "normal", "horizon_days": 7,
            "scenario_assumptions": [{"loss_scenario_ref": simulation["simulation_id"],
                                      "probability_over_horizon": "0.02"}],
            "exit_quote_id": snapshot.quotes[0].quote_id, "estimated_net_carry_usd": "1000",
            "carry_explanation": "Synthetic seven-day net carry assumption for the jury demo",
        }
        response = api.post("/api/v1/decisions", json=body)
        assert response.status_code == 200
        result = response.json()["data"]
        assert result["decision_status"] == "CONDITIONAL" and result["verdict"] == "STAY"
        assert Decimal(result["stay_value_usd"]) == 793000
        assert Decimal(result["exit_value_usd"]) == 779900
        assert Decimal(result["break_even_probability"]) == Decimal("0.05275")
        assert result["explanation"] and result["warnings"]
        assert any("SYNTHETIC" in w for w in response.json()["warnings"])
        body["scenario_assumptions"][0]["probability_over_horizon"] = "0.10"
        assert api.post("/api/v1/decisions", json=body).json()["data"]["verdict"] == "EXIT"


def test_real_decision_rejects_demo_simulation_even_with_matching_ids():
    demo = load_snapshot(DEFAULT_SNAPSHOT)
    result = AnalyticsService(demo).simulate(request(demo, "UNAUTHORIZED_MINTING"))
    real = real_fixture()
    decision = decide(real, decision_input(real, result), {result.simulation_id: result}, real.as_of)
    assert decision.decision_status == "INSUFFICIENT_DATA"
    assert "MIXED_DATA_MODE_NOT_COMPARABLE" in decision.warnings
    assert decision.verdict is None and decision.stay_value_usd is None
    assert decision.expected_loss_usd is None and decision.exit_value_usd is None
    assert "_data_mode" not in result.model_dump()
    assert "_data_mode" not in SimulationResult.model_json_schema()["properties"]


@pytest.mark.parametrize("subject", ["position", "price_proof"])
def test_mixed_valuation_inputs_block_stress_and_decision(subject):
    snapshot = real_fixture()
    payload = snapshot.model_dump(mode="json")
    if subject == "position":
        payload["positions"][0]["data_mode"] = "DEMO"
    else:
        proof_id = snapshot.market(snapshot.positions[0]).morpho.collateral_price.evidence_ids[0]
        next(e for e in payload["evidence"] if e["evidence_id"] == proof_id)["data_mode"] = "DEMO"
    mixed = AnalyticsSnapshot.model_validate(payload)
    service = AnalyticsService(mixed)
    assert value_position(mixed, mixed.positions[0]).equity_usd is None
    result = service.simulate(request(mixed, "UNAUTHORIZED_MINTING"))
    assert result.scenario_estimated_loss_usd is None
    assert "MIXED_DATA_MODE_NOT_COMPARABLE" in result.warnings
    decision = service.decide(decision_input(mixed, result))
    assert decision.decision_status == "INSUFFICIENT_DATA" and decision.verdict is None
    assert "MIXED_DATA_MODE_NOT_COMPARABLE" in decision.warnings


def test_forecast_proof_must_match_real_position_mode():
    snapshot = real_fixture()
    bundle = forecast(snapshot)
    payload = snapshot.model_dump(mode="json")
    payload["forecasts"] = [bundle.model_dump(mode="json")]
    next(e for e in payload["evidence"] if e["evidence_id"] == bundle.evidence_ids[0])["data_mode"] = "DEMO"
    mixed = AnalyticsSnapshot.model_validate(payload)
    req = request(snapshot, "UNAUTHORIZED_MINTING")
    assert forecast_carry(mixed, req, snapshot.as_of) == (None, "NOT_VALIDATED", None)


def test_real_closing_api_reports_funding_sale_cost_and_remaining_equity_once():
    snapshot = real_fixture()
    payload = snapshot.model_dump(mode="json")
    payload["quotes"][0].update(closing_feasible=None, repayment_liquidity_confirmed=False,
        gas_cost_usd=None, extra_fees_usd=None)
    snapshot = AnalyticsSnapshot.model_validate(payload)
    position = snapshot.positions[0]
    body = dict(strategy_id=position.strategy_id, position_id=position.position_id,
        exit_quote_id=snapshot.quotes[0].quote_id, assumptions={
            "available_repayment_loan_units": "1200000", "gas_cost_usd": "100",
            "extra_fees_usd": "20", "financing_fee_usd": "30",
            "explanation": "Synthetic funding and complete operation costs for a REAL-shaped test",
        })
    with TestClient(create_app(snapshot=snapshot, replay=True)) as api:
        response = api.post("/api/v1/closing-plans", json=body)
        assert response.status_code == 200
        result = response.json()["data"]
        assert result["status"] == "CONDITIONAL" and result["basis"] == "USER_ASSUMPTION"
        assert Decimal(result["available_repayment_loan_units"]) == 1200000
        assert Decimal(result["repayment_shortfall_loan_units"]) == 0
        assert Decimal(result["sale_proceeds_usd"]) == 1980000
        assert Decimal(result["total_operation_cost_usd"]) == 150
        assert Decimal(result["net_proceeds_usd"]) == 1979850
        assert Decimal(result["exit_cost_usd"]) == 20150
        assert Decimal(result["net_equity_after_exit_usd"]) == 779850
        assert "EXTERNAL_FUNDING_AND_COMPLETE_CLOSING_COSTS_ARE_USER_ASSUMPTIONS" in result["warnings"]
        no_funding = {k: v for k, v in body.items() if k != "assumptions"}
        missing = api.post("/api/v1/closing-plans", json=no_funding).json()["data"]
        assert missing["status"] == "EXIT_NOT_FEASIBLE"
        assert missing["net_equity_after_exit_usd"] is None
        assert "repayment_liquidity_confirmed" in missing["missing_fields"]
        body["assumptions"]["available_repayment_loan_units"] = "0"
        unfunded = api.post("/api/v1/closing-plans", json=body).json()["data"]
        assert unfunded["status"] == "EXIT_NOT_FEASIBLE"
        assert Decimal(unfunded["repayment_shortfall_loan_units"]) == 1200000


@pytest.mark.parametrize("fault", ["absent", "age", "amount", "below_debt"])
def test_exit_never_succeeds_without_fresh_exact_quote_covering_debt(fault):
    snapshot = real_fixture()
    payload = snapshot.model_dump(mode="json")
    quote = payload["quotes"][0]["quote"]
    if fault == "absent":
        payload["quotes"] = []
    elif fault == "age":
        quote["observed_at"] = (snapshot.as_of-timedelta(minutes=5)).isoformat()
    elif fault == "amount":
        quote["input_amount"] = "1000000"
        quote["output_amount"] = "990000"
    else:
        quote["output_amount"] = "1000000"
        quote["value"] = "0.5"
    changed = AnalyticsSnapshot.model_validate(payload)
    status, cost, _, _ = evaluate_exit(changed, changed.positions[0], snapshot.quotes[0].quote_id, snapshot.as_of)
    assert status == ("EXIT_NOT_FEASIBLE" if fault == "below_debt" else "QUOTE_UNAVAILABLE")
    assert cost is None


def test_forecast_expiration_rechecked_on_every_decision_with_identical_normal_fallback():
    snapshot = load_snapshot(DEFAULT_SNAPSHOT)
    deadline = snapshot.as_of+timedelta(minutes=1)
    bundle = forecast(snapshot, valid_until=deadline)
    snapshot = snapshot.model_copy(update={"forecasts": (bundle,)})
    now = [snapshot.as_of]
    with TestClient(create_app(snapshot=snapshot, replay=False, clock=lambda: now[0])) as api:
        response = api.post("/api/v1/simulations", json=request(snapshot, "UNAUTHORIZED_MINTING").model_dump(mode="json"))
        simulation = SimulationResult.model_validate(response.json()["data"])
        body = decision_input(snapshot, simulation, mode="predictive").model_dump(mode="json")
        first = api.post("/api/v1/decisions", json=body).json()["data"]
        assert first["effective_mode"] == "predictive" and first["fallback_reason"] is None
        now[0] = deadline
        assert forecast_carry(snapshot, decision_input(snapshot, simulation), deadline) == (None, "STALE", None)
        expired = api.post("/api/v1/decisions", json=body).json()["data"]
        assert expired["effective_mode"] == "normal" and expired["fallback_reason"] == "STALE"
        assert expired["forecast_bundle_id"] is None
        normal = api.post("/api/v1/decisions", json={**body, "mode": "normal"}).json()["data"]
        assert expired["decision_status"] == normal["decision_status"] == "CONDITIONAL"
        for field in ("verdict", "stay_value_usd", "exit_value_usd", "net_carry_usd", "break_even_probability"):
            assert expired[field] == normal[field]


@pytest.mark.parametrize("expiry_offset", [6, 7])
def test_carry_does_not_extrapolate_through_or_beyond_pt_maturity(expiry_offset):
    snapshot = load_snapshot(DEFAULT_SNAPSHOT)
    payload = snapshot.model_dump(mode="json")
    payload["strategies"][0]["expiry"] = (snapshot.as_of+timedelta(days=expiry_offset)).isoformat()
    snapshot = AnalyticsSnapshot.model_validate(payload)
    assumptions = CarryAssumptions(pt_annual_return="0.10", borrow_apy="0.05", holding_fees_usd="0",
        explanation="Synthetic rates must never bypass the PT maturity guard")
    result = estimate_carry(snapshot, snapshot.positions[0].strategy_id, snapshot.positions[0].position_id,
                            7, snapshot.as_of, assumptions)
    assert result.status == "INSUFFICIENT_DATA" and result.net_carry_usd is None
    assert result.collateral_return_usd is None and result.borrowing_cost_usd is None
    assert "HORIZON_BEYOND_PT_EXPIRY" in result.warnings


@pytest.mark.parametrize("source", ["manual", "rates", "forecast"])
def test_decision_cannot_bypass_pt_maturity_with_any_carry_source(source):
    snapshot = load_snapshot(DEFAULT_SNAPSHOT)
    if source == "forecast":
        snapshot = snapshot.model_copy(update={"forecasts": (forecast(snapshot, horizon_days=365),)})
    service = AnalyticsService(snapshot)
    result = service.simulate(request(snapshot, "UNAUTHORIZED_MINTING", horizon_days=365))
    updates = {}
    if source == "rates":
        updates = dict(estimated_net_carry_usd=None, carry_assumptions=CarryAssumptions(
            pt_annual_return="0.10", borrow_apy="0.05", holding_fees_usd="0",
            explanation="Synthetic rate assumptions over an unsupported post-maturity horizon"))
    elif source == "forecast":
        updates = dict(mode="predictive")
    decision = service.decide(decision_input(snapshot, result, **updates))
    assert decision.decision_status == "INSUFFICIENT_DATA"
    assert "HORIZON_BEYOND_PT_EXPIRY" in decision.warnings
    assert decision.stay_value_usd is None and decision.net_carry_usd is None
    assert decision.verdict is None and decision.break_even_probability is None
    assert decision.forecast_bundle_id is None
