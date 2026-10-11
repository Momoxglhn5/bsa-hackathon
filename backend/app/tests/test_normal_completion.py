"""Synthetic end-to-end Normal decisions, funding and source-preserving storage."""
from datetime import timedelta
from decimal import Decimal, localcontext

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from app.analytics.dataset import AnalyticsSnapshot, load_snapshot
from app.analytics.service import AnalyticsService
from app.decision.carry import CarryAssumptions, estimate_carry
from app.decision.closing import ClosingAssumptions, closing_plan
from app.decision.exit_cost import evaluate_exit
from app.main import DEFAULT_SNAPSHOT, create_app
from app.models.timeseries import TimeseriesDTO
from app.risk.scoring import current_risk
from app.storage.parquet_store import SCHEMA, ParquetStore, observations_from_series, _to_table
from app.tests.test_core_analytics import request, decision_input


@pytest.fixture
def snapshot():
    data = load_snapshot(DEFAULT_SNAPSHOT).model_dump(mode="json")
    data["markets"][1]["pendle"]["implied_apy"] = "0.1"
    return AnalyticsSnapshot.model_validate(data)


def funding(snapshot, **updates):
    return ClosingAssumptions.model_validate(dict(
        available_repayment_loan_units=str(snapshot.positions[0].borrow_amount),
        gas_cost_usd="100", extra_fees_usd="20", financing_fee_usd="30",
        explanation="Synthetic external repayment funding and total closing costs", **updates))


def partial_quote(snapshot):
    q = snapshot.quotes[0].model_copy(update={"closing_feasible": None, "gas_cost_usd": None,
        "extra_fees_usd": None, "repayment_liquidity_confirmed": False})
    return snapshot.model_copy(update={"quotes": (q,)})


def test_current_effective_rates_compute_net_carry_without_double_yield(snapshot):
    result = estimate_carry(snapshot, snapshot.strategies[0].strategy_id,
                            snapshot.positions[0].position_id, 7, snapshot.as_of)
    assert result.status == "CONDITIONAL"
    with localcontext() as ctx:
        ctx.prec = 60
        assert result.net_carry_usd == result.collateral_return_usd-result.borrowing_cost_usd
    assert result.pt_annual_return == Decimal("0.1")
    assert result.borrow_apy == Decimal("0.05")
    assert result.basis == "CONSTANT_CURRENT_RATES"
    assert "ADDITIONAL_HOLDING_FEES_ASSUMED_ZERO" in result.warnings


def test_zero_explicit_rates_charge_only_holding_fees(snapshot):
    assumptions = CarryAssumptions(pt_annual_return="0", borrow_apy="0", holding_fees_usd="12",
                                   explanation="Explicit synthetic zero rates")
    result = estimate_carry(snapshot, snapshot.strategies[0].strategy_id,
                            snapshot.positions[0].position_id, 7, snapshot.as_of, assumptions)
    assert result.net_carry_usd == -12
    assert result.basis == "USER_ASSUMPTION"


@pytest.mark.parametrize("cause", ["missing_rate", "stale", "expiry", "wrong_mode"])
def test_carry_cannot_publish_from_unusable_inputs(snapshot, cause):
    data = snapshot.model_dump(mode="json")
    now = snapshot.as_of
    if cause == "missing_rate":
        data["markets"][1]["pendle"]["implied_apy"] = None
    elif cause == "stale":
        now += timedelta(minutes=6)
    elif cause == "expiry":
        now = snapshot.strategies[0].expiry-timedelta(days=3)
    else:
        data["evidence"][0].update(data_mode="REAL", source_kind="MORPHO_API")
    result = estimate_carry(AnalyticsSnapshot.model_validate(data), snapshot.strategies[0].strategy_id,
                            snapshot.positions[0].position_id, 7, now)
    assert result.status == "INSUFFICIENT_DATA"
    assert result.net_carry_usd is None


def test_partial_quote_requires_plan_and_costs(snapshot):
    snapshot = partial_quote(snapshot)
    p = snapshot.positions[0]
    missing = closing_plan(snapshot, p.strategy_id, p.position_id, snapshot.quotes[0].quote_id, snapshot.as_of)
    assert missing.status == "EXIT_NOT_FEASIBLE"
    assert "closing_feasible" in missing.missing_fields
    assumed = closing_plan(snapshot, p.strategy_id, p.position_id, snapshot.quotes[0].quote_id,
                           snapshot.as_of, funding(snapshot))
    assert assumed.status == "CONDITIONAL"
    assert assumed.basis == "USER_ASSUMPTION"
    assert len(assumed.steps) == 5
    # Funding principal is recovered, not charged a second time as an exit fee.
    q = snapshot.quotes[0].quote
    spot = snapshot.market(p).morpho
    expected_cost = p.collateral_amount*spot.collateral_price.value-q.output_amount*spot.loan_price.value+150
    assert assumed.exit_cost_usd == expected_cost


def test_insufficient_external_funding_is_not_feasible(snapshot):
    assumptions = funding(snapshot).model_copy(update={"available_repayment_loan_units": Decimal("1")})
    snapshot = partial_quote(snapshot)
    p = snapshot.positions[0]
    result = closing_plan(snapshot, p.strategy_id, p.position_id, snapshot.quotes[0].quote_id,
                          snapshot.as_of, assumptions)
    assert result.status == "EXIT_NOT_FEASIBLE"
    assert result.net_equity_after_exit_usd is None


@pytest.mark.parametrize("field", ["route_available", "closing_feasible"])
def test_assumptions_never_override_a_known_failed_route(snapshot, field):
    snapshot = partial_quote(snapshot)
    snapshot = snapshot.model_copy(update={"quotes": (snapshot.quotes[0].model_copy(update={field: False}),)})
    p = snapshot.positions[0]
    result = closing_plan(snapshot, p.strategy_id, p.position_id, snapshot.quotes[0].quote_id,
                          snapshot.as_of, funding(snapshot))
    assert result.status == "EXIT_NOT_FEASIBLE"


def test_expired_quote_is_rejected_at_exact_deadline(snapshot):
    q = snapshot.quotes[0]
    assert evaluate_exit(snapshot, snapshot.positions[0], q.quote_id, q.quote.valid_until)[0] == "QUOTE_UNAVAILABLE"


def test_normal_decision_without_manual_carry_uses_current_rates_and_assumed_closing(snapshot):
    snapshot = partial_quote(snapshot)
    service = AnalyticsService(snapshot)
    sim = service.simulate(request(snapshot, "UNAUTHORIZED_MINTING"))
    req = decision_input(snapshot, sim, estimated_net_carry_usd=None, closing_assumptions=funding(snapshot))
    result = service.decide(req)
    assert result.decision_status == "CONDITIONAL"
    assert result.verdict in {"STAY", "EXIT"}
    assert result.effective_mode == "normal"
    assert result.carry_calculation.status == "CONDITIONAL"
    assert result.closing_plan.basis == "USER_ASSUMPTION"
    assert "CLOSING_PLAN_AND_FUNDING_ARE_USER_ASSUMPTIONS_NOT_VERIFIED_EXECUTION" in result.warnings


def test_financial_preview_endpoints_share_the_same_calculations(snapshot):
    client = TestClient(create_app(snapshot=snapshot))
    p = snapshot.positions[0]
    body = dict(strategy_id=p.strategy_id, position_id=p.position_id)
    carry = client.post("/api/v1/carry-estimates", json={**body, "horizon_days": 7})
    close = client.post("/api/v1/closing-plans", json={**body, "exit_quote_id": snapshot.quotes[0].quote_id,
                        "assumptions": funding(snapshot).model_dump(mode="json")})
    assert carry.status_code == close.status_code == 200
    assert carry.json()["status"] == close.json()["status"] == "CONDITIONAL"
    assert client.post("/api/v1/carry-estimates", json={**body, "horizon_days": 0}).status_code == 422
    assert client.post("/api/v1/closing-plans", json={**body, "assumptions": {}}).status_code == 422


def test_risk_cannot_use_real_evidence_for_demo_market(snapshot):
    data = snapshot.model_dump(mode="json")
    data["evidence"][0].update(data_mode="REAL", source_kind="MORPHO_API")
    result = current_risk(AnalyticsSnapshot.model_validate(data), snapshot.strategies[0].strategy_id, snapshot.as_of)
    assert all(f.risk is None for f in result.factor_explanations)


def test_parquet_keeps_point_proofs_and_daily_interval_and_reads_legacy_files(tmp_path):
    from app.tests.test_parquet_store import implied_apy_series, PENDLE_ENTITY, NOW
    dto = implied_apy_series()
    evidence = "ev_"+"a"*24
    dto = dto.model_copy(update={"points": tuple(p.model_copy(update={"evidence_ids": (evidence,)}) for p in dto.points)})
    rows = observations_from_series(dto, PENDLE_ENTITY, NOW)
    store = ParquetStore(tmp_path)
    path = store.write_observations(rows, dto.data_mode)
    loaded = store.read_series(PENDLE_ENTITY, dto.metric.value, dto.data_mode)
    assert loaded.interval_seconds == 86400
    assert all(p.evidence_ids == (evidence,) for p in loaded.points)
    # Old files remain readable without inventing proofs or a sampling frequency.
    legacy = _to_table(rows).select([f.name for f in SCHEMA if f.name not in {"evidence_ids", "interval_seconds"}])
    pq.write_table(legacy, path)
    loaded = store.read_series(PENDLE_ENTITY, dto.metric.value, dto.data_mode)
    assert loaded.interval_seconds is None
    assert all(p.evidence_ids == () for p in loaded.points)
