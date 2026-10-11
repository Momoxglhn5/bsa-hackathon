"""Synthetic fixtures exercise financial invariants, never assert live verification."""
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.analytics.dataset import AnalyticsSnapshot, load_snapshot
from app.analytics.service import AnalyticsService
from app.analytics.valuation import value_position
from app.decision.schemas import DecisionRequest
from app.graph.builder import build_graph
from app.graph.exposure import exposure
from app.graph.propagation import propagate
from app.graph.types import Edge, GraphDTO, Node, Fallback
from app.main import DEFAULT_SNAPSHOT, create_app
from app.models.evidence import EvidenceDTO
from app.risk.scoring import FACTORS, Factor, current_risk, score_factors
from app.stress.schemas import SimulationRequest


@pytest.fixture
def snapshot():
    return load_snapshot(DEFAULT_SNAPSHOT)


def revalidate(snapshot, **updates):
    data = snapshot.model_dump(mode='json')
    data.update(updates)
    return AnalyticsSnapshot.model_validate(data)


def request(snapshot, scenario='ORACLE_MANIPULATION', **updates):
    p = snapshot.positions[0]
    data = dict(strategy_id=p.strategy_id, position_id=p.position_id, scenario_type=scenario,
                target_node_id=f'oracle:1:{snapshot.market(p).morpho.oracle_address}',
                parameters={'oracle_price_change_ratio': '-0.4'} if scenario == 'ORACLE_MANIPULATION' else {})
    if scenario == 'UNAUTHORIZED_MINTING':
        data.update(target_node_id=p.collateral_asset.asset_id, parameters={'assumed_token_haircut_ratio': '0.2'})
    data.update(updates)
    return SimulationRequest.model_validate(data)


def decision_input(snapshot, result, **updates):
    data = dict(strategy_id=result.strategy_id, position_id=result.position_id, horizon_days=result.horizon_days,
                scenario_assumptions=[dict(loss_scenario_ref=result.simulation_id, probability_over_horizon='0.02')],
                exit_quote_id=snapshot.quotes[0].quote_id, estimated_net_carry_usd='1000',
                carry_explanation='Synthetic expected PT carry minus borrowing costs over seven days')
    data.update(updates)
    return DecisionRequest.model_validate(data)


def test_snapshot_roundtrip(snapshot):
    assert AnalyticsSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
    assert all(m.data_mode == 'DEMO' for m in snapshot.markets)


def test_graph_orientation_and_provenance(snapshot):
    g = build_graph(snapshot, snapshot.strategies[0].strategy_id)
    oracle = next(n.id for n in g.nodes if n.type == 'ORACLE')
    market = snapshot.markets[0].market_key
    assert any(e.source == market and e.target == oracle and e.relation_type == 'USES_ORACLE' for e in g.edges)
    assert all(e.verification_status == 'HYPOTHESIS' for e in g.edges)
    assert all(e.evidence_ids for e in g.edges)
    assert g == build_graph(snapshot, g.strategy_id)
    impact = propagate(snapshot, g, [oracle], 'VALUATION')
    assert impact.potential_position_ids == ('pos_demo_fund',)
    assert not impact.confirmed_position_ids


def test_wrong_pt_does_not_join(snapshot):
    data = snapshot.model_dump(mode='json')
    data['markets'][1]['pendle']['pt']['address'] = '0x' + '9'*40
    data['markets'][1]['pendle']['pt_price'] = None
    changed = AnalyticsSnapshot.model_validate(data)
    g = build_graph(changed, changed.strategies[0].strategy_id)
    assert not any(e.relation_type == 'REDEEMS_THROUGH' for e in g.edges)


def test_chain_mismatch_rejected(snapshot):
    data = snapshot.model_dump(mode='json')
    data['positions'][0]['collateral_asset']['chain_id'] = 2
    with pytest.raises(ValidationError):
        AnalyticsSnapshot.model_validate(data)


def test_duplicate_positions_rejected(snapshot):
    with pytest.raises(ValidationError):
        revalidate(snapshot, positions=[p.model_dump(mode='json') for p in snapshot.positions]*2)


def test_missing_evidence_never_confirms(snapshot):
    g = build_graph(revalidate(snapshot, evidence=[]), snapshot.strategies[0].strategy_id)
    assert all(e.verification_status != 'CONFIRMED' for e in g.edges)


def graph_fixture(snapshot, *, fallbacks=()):
    nodes = tuple(Node(id=n, type='POSITION' if n.startswith('pos_') else 'ORACLE', label=n)
                  for n in ['pos_demo_fund', 'a', 'b', 'c'])
    edges = tuple(Edge(id=f'e{i}', source=s, target=t, relation_type='USES_ORACLE',
                       verification_status='CONFIRMED', failure_modes=('ECONOMIC',),
                       fallbacks=fallbacks if s == 'pos_demo_fund' else (), as_of=snapshot.as_of)
                  for i, (s, t) in enumerate([('pos_demo_fund', 'a'), ('pos_demo_fund', 'b'),
                                              ('a', 'c'), ('b', 'c'), ('c', 'a')]))
    return GraphDTO(strategy_id=snapshot.strategies[0].strategy_id, snapshot_as_of=snapshot.as_of, nodes=nodes, edges=edges)


def test_cycles_multiple_paths_multiple_failures(snapshot):
    impact = propagate(snapshot, graph_fixture(snapshot), ['c', 'b'])
    assert impact.confirmed_position_ids == ('pos_demo_fund',)
    assert exposure(snapshot, impact.confirmed_position_ids*3) == Decimal('800000')
    assert len(impact.representative_paths['pos_demo_fund']) <= 4


def test_unknown_fallback_is_potential(snapshot):
    graph = graph_fixture(snapshot, fallbacks=(Fallback(node_id='backup'),))
    impact = propagate(snapshot, graph, ['c'])
    assert not impact.confirmed_position_ids
    assert impact.potential_position_ids == ('pos_demo_fund',)


def test_verified_healthy_fallback_blocks(snapshot):
    ev = EvidenceDTO(chain_id=1, data_mode='REAL', evidence_id='ev_'+'b'*24, source_kind='RPC',
                     source_id='test:rpc', method='synthetic RPC-shaped test only', block_number=1,
                     fetched_at=snapshot.as_of, status='OK')
    snap = revalidate(snapshot, evidence=[ev.model_dump(mode='json')])
    graph = graph_fixture(snap, fallbacks=(Fallback(node_id='backup', state='HEALTHY', evidence_ids=(ev.evidence_id,)),))
    assert not propagate(snap, graph, ['c']).confirmed_position_ids
    assert propagate(snap, graph, ['c']).affected_node_ids == ('a', 'b', 'c')


def test_optional_dependency_does_not_propagate(snapshot):
    graph = graph_fixture(snapshot)
    graph = graph.model_copy(update={'edges': tuple(e.model_copy(update={'required': False}) for e in graph.edges)})
    assert propagate(snapshot, graph, ['c']).affected_node_ids == ('c',)


def test_oracle_shock_hf_and_no_invented_loss(snapshot):
    result = AnalyticsService(snapshot).simulate(request(snapshot))
    effect = result.position_effects['pos_demo_fund']
    assert effect.before.ltv_ratio == Decimal('0.6')
    assert effect.after.ltv_ratio == 1
    assert effect.after.health_factor == Decimal('0.915')
    assert effect.after.liquidatable is True
    assert effect.after.equity_usd == effect.before.equity_usd == Decimal('800000')
    assert result.scenario_estimated_loss_usd is None
    assert result.potentially_exposed_capital_usd == Decimal('800000')
    assert result.gross_exposure_usd == Decimal('3200000')


def test_zero_debt_not_liquidatable(snapshot):
    data = snapshot.model_dump(mode='json')
    data['positions'][0]['borrow_amount'] = '0'
    snap = AnalyticsSnapshot.model_validate(data)
    result = AnalyticsService(snap).simulate(request(snap))
    assert result.position_effects['pos_demo_fund'].after.liquidatable is False
    assert result.position_effects['pos_demo_fund'].after.health_factor is None


def test_zero_oracle_price(snapshot):
    result = AnalyticsService(snapshot).simulate(request(snapshot, parameters={'oracle_price_change_ratio': '-1'}))
    after = result.position_effects['pos_demo_fund'].after
    assert after.liquidatable is True
    assert after.health_factor == 0
    assert after.ltv_ratio is None  # undefined, not Infinity in JSON


def test_outage_does_not_liquidate_or_create_loss(snapshot):
    result = AnalyticsService(snapshot).simulate(request(snapshot, 'ORACLE_OUTAGE'))
    assert result.operationally_blocked_usd == Decimal('800000')
    assert result.scenario_estimated_loss_usd is None
    assert result.position_effects['pos_demo_fund'].before == result.position_effects['pos_demo_fund'].after


@pytest.mark.parametrize('params', [{'mint_amount': '999999999999'}, {'backing_impairment_ratio': '0.5'}])
def test_minting_without_haircut_does_not_invent_loss(snapshot, params):
    result = AnalyticsService(snapshot).simulate(request(snapshot, 'UNAUTHORIZED_MINTING', parameters=params))
    assert result.scenario_estimated_loss_usd is None


def test_haircut_does_not_change_oracle(snapshot):
    result = AnalyticsService(snapshot).simulate(request(snapshot, 'UNAUTHORIZED_MINTING'))
    effect = result.position_effects['pos_demo_fund']
    assert effect.scenario_estimated_loss_usd == Decimal('400000')
    assert effect.after.equity_usd == Decimal('400000')
    assert effect.after.health_factor == effect.before.health_factor


def test_cross_chain_without_bridge_not_applicable(snapshot):
    result = AnalyticsService(snapshot).simulate(request(snapshot, 'CROSS_CHAIN_FAILURE', target_node_id='bridge:missing', scenario_scope='VERIFIED_TARGET'))
    assert result.scenario_status == 'NOT_APPLICABLE'
    assert result.scenario_estimated_loss_usd is None


def test_hypothetical_bridge_is_isolated(snapshot):
    n = Node(id='bridge:hypothetical', type='BRIDGE', label='Test only')
    e = Edge(id='hypothesis', source=snapshot.positions[0].collateral_asset.asset_id, target=n.id,
             relation_type='BRIDGED_BY', verification_status='HYPOTHESIS', failure_modes=('LIVENESS',), as_of=snapshot.as_of)
    snap = revalidate(snapshot, additional_nodes=[n.model_dump(mode='json')], additional_edges=[e.model_dump(mode='json')])
    assert n.id not in {n.id for n in build_graph(snap, snap.strategies[0].strategy_id).nodes}
    result = AnalyticsService(snap).simulate(request(snap, 'CROSS_CHAIN_FAILURE', target_node_id=n.id))
    assert result.scenario_scope == 'HYPOTHETICAL'
    assert result.operationally_blocked_usd == Decimal('800000')
    assert not result.confirmed_exposed_capital_usd


def test_decision_correct_baseline_and_break_even(snapshot):
    service = AnalyticsService(snapshot)
    scenario = service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING'))
    result = service.decide(decision_input(snapshot, scenario))
    assert result.decision_status == 'CONDITIONAL'
    assert result.verdict == 'STAY'
    assert result.equity_now_usd == Decimal('800000')
    assert result.expected_loss_usd == Decimal('8000')
    assert result.stay_value_usd == Decimal('793000')
    assert result.exit_value_usd == Decimal('779900')
    assert result.exit_cost_usd == Decimal('20100')  # impact is not counted twice
    assert result.break_even_probability == Decimal('0.05275')
    req = decision_input(snapshot, scenario, scenario_assumptions=[dict(loss_scenario_ref=scenario.simulation_id, probability_over_horizon='0.1')])
    assert service.decide(req).verdict == 'EXIT'


def test_exact_tie_no_arbitrary_verdict(snapshot):
    service = AnalyticsService(snapshot)
    scenario = service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING'))
    result = service.decide(decision_input(snapshot, scenario, scenario_assumptions=[dict(loss_scenario_ref=scenario.simulation_id, probability_over_horizon='0.05275')]))
    assert result.comparison == 'EQUAL'
    assert result.verdict is None


def test_predictive_fallback_numbers_identical(snapshot):
    service = AnalyticsService(snapshot)
    scenario = service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING'))
    normal = service.decide(decision_input(snapshot, scenario)).model_dump()
    predictive = service.decide(decision_input(snapshot, scenario, mode='predictive')).model_dump()
    assert predictive.pop('fallback_reason') == 'FORECAST_UNAVAILABLE'
    assert predictive.pop('requested_mode') == 'predictive'
    normal.pop('requested_mode'); normal.pop('fallback_reason')
    assert normal == predictive


def test_unquantified_loss_even_zero_probability_blocks(snapshot):
    service = AnalyticsService(snapshot)
    scenario = service.simulate(request(snapshot))
    result = service.decide(decision_input(snapshot, scenario, scenario_assumptions=[dict(loss_scenario_ref=scenario.simulation_id, probability_over_horizon='0')]))
    assert result.decision_status == 'INSUFFICIENT_DATA'
    assert result.expected_loss_usd is None and result.verdict is None


@pytest.mark.parametrize('probability', ['-0.1', '1.01', 'NaN', 0.1])
def test_invalid_probability(snapshot, probability):
    service = AnalyticsService(snapshot)
    scenario = service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING'))
    with pytest.raises(ValidationError):
        decision_input(snapshot, scenario, scenario_assumptions=[dict(loss_scenario_ref=scenario.simulation_id, probability_over_horizon=probability)])


def test_quote_size_mismatch(snapshot):
    data = snapshot.model_dump(mode='json')
    data['positions'][0]['collateral_amount'] = '5000000'
    snap = AnalyticsSnapshot.model_validate(data)
    service = AnalyticsService(snap)
    result = service.decide(decision_input(snap, service.simulate(request(snap, 'UNAUTHORIZED_MINTING'))))
    assert result.decision_status == 'QUOTE_UNAVAILABLE'
    assert result.exit_cost_usd is None


@pytest.mark.parametrize('change,status', [({'gas_cost_usd': None}, 'INSUFFICIENT_DATA'),
    ({'route_available': False}, 'EXIT_NOT_FEASIBLE'), ({'closing_feasible': None}, 'INSUFFICIENT_DATA'),
    ({'repayment_liquidity_confirmed': False}, 'EXIT_NOT_FEASIBLE')])
def test_exit_requirements(snapshot, change, status):
    data = snapshot.model_dump(mode='json')
    data['quotes'][0].update(change)
    snap = AnalyticsSnapshot.model_validate(data)
    service = AnalyticsService(snap)
    result = service.decide(decision_input(snap, service.simulate(request(snap, 'UNAUTHORIZED_MINTING'))))
    assert result.decision_status == status
    assert result.verdict is None


def test_stale_quote_live_vs_replay(snapshot):
    service = AnalyticsService(snapshot, replay=False, clock=lambda: snapshot.as_of+timedelta(minutes=6))
    result = service.decide(decision_input(snapshot, service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING'))))
    assert result.quote_status == 'QUOTE_UNAVAILABLE'


def test_missing_quote(snapshot):
    service = AnalyticsService(snapshot)
    result = service.decide(decision_input(snapshot, service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING')), exit_quote_id=None))
    assert result.decision_status == 'QUOTE_UNAVAILABLE'
    assert result.exit_cost_usd is None


@pytest.mark.parametrize('carry,expected', [('9999999', 'NO_REVERSAL_IN_FEASIBLE_RANGE'), ('-50000', 'NO_REVERSAL_IN_FEASIBLE_RANGE')])
def test_break_even_outside_range(snapshot, carry, expected):
    service = AnalyticsService(snapshot)
    result = service.decide(decision_input(snapshot, service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING')), estimated_net_carry_usd=carry))
    assert result.break_even_status == expected
    assert result.break_even_probability < 0 or result.break_even_probability > 1


def test_zero_conditional_loss(snapshot):
    service = AnalyticsService(snapshot)
    result = service.decide(decision_input(snapshot, service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING', parameters={'assumed_token_haircut_ratio': '0'}))))
    assert result.break_even_status == 'NO_SENSITIVITY_ZERO_LOSS'
    assert result.expected_loss_usd == 0


def test_horizon_mismatch(snapshot):
    service = AnalyticsService(snapshot)
    scenario = service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING'))
    with pytest.raises(ValueError, match='horizon'):
        service.decide(decision_input(snapshot, scenario, horizon_days=30))


def test_multi_scenario_exclusivity_and_sensitivity(snapshot):
    service = AnalyticsService(snapshot)
    a = service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING'))
    b = service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING', parameters={'assumed_token_haircut_ratio': '0.1'}))
    assumptions = [dict(loss_scenario_ref=r.simulation_id, probability_over_horizon='0.02') for r in (a, b)]
    req = decision_input(snapshot, a, scenario_assumptions=assumptions)
    assert service.decide(req).decision_status == 'INSUFFICIENT_DATA'
    result = service.decide(req.model_copy(update={'scenarios_mutually_exclusive': True}))
    assert result.expected_loss_usd == Decimal('12000')
    assert result.break_even_probability == Decimal('0.04275')
    assert result.sensitivity_probability_max == Decimal('0.98')


def test_bounded_cache(snapshot):
    service = AnalyticsService(snapshot, max_simulations=1)
    old = service.simulate(request(snapshot))
    service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING'))
    with pytest.raises(KeyError):
        service.decide(decision_input(snapshot, old))


def test_risk_unknowns_and_liquidation_separate(snapshot):
    result = current_risk(snapshot, snapshot.strategies[0].strategy_id, snapshot.as_of)
    assert result.completeness_pct == 30
    assert result.risk_index is None
    assert result.evidence_confidence_pct == 0  # synthetic evidence
    assert len(result.unknown_factors) == 7
    assert result.position_risk['pos_demo_fund'].ltv_ratio == Decimal('0.6')


def factors(known):
    return [Factor(factor_id=id, dimension=dim, risk='0.8' if id in known else None,
                   source_quality='1' if id in known else None, freshness='1' if id in known else None,
                   explanation='Explicit synthetic test metric') for id, dim in FACTORS.items()]


def test_risk_publication_threshold():
    result = score_factors(factors(['L_UTILIZATION', 'S_AUDIT']))
    assert result.completeness_pct == 20 and result.risk_index is None
    known = ['L_UTILIZATION', 'L_TVL_DRAWDOWN', 'S_AUDIT', 'S_SOURCE_VERIFIED', 'G_CONTROL']
    result = score_factors(factors(known))
    assert result.risk_index == 80  # missing factors do not lower risk to 40
    assert result.completeness_pct == 50


def test_stale_evidence_lowers_confidence(snapshot):
    data = snapshot.model_dump(mode='json')
    data['evidence'][0].update(source_kind='DERIVED')
    snap = AnalyticsSnapshot.model_validate(data)
    fresh = current_risk(snap, snap.strategies[0].strategy_id, snap.as_of)
    stale = current_risk(snap, snap.strategies[0].strategy_id, snap.as_of+timedelta(hours=12))
    assert stale.evidence_confidence_pct < fresh.evidence_confidence_pct
    assert stale.factor_explanations[0].risk == fresh.factor_explanations[0].risk


def test_api_end_to_end(snapshot):
    client = TestClient(create_app(snapshot))
    sid = snapshot.strategies[0].strategy_id
    for route in [f'graph/{sid}', f'risk/{sid}', 'scenarios', 'incident-frequencies']:
        response = client.get('/api/v1/'+route)
        assert response.status_code == 200, response.text
        assert set(response.json()) == {'schema_version', 'status', 'as_of', 'data', 'provenance', 'missing_fields', 'warnings'}
    sim = client.post('/api/v1/simulations', json=request(snapshot, 'UNAUTHORIZED_MINTING').model_dump(mode='json'))
    assert sim.status_code == 200, sim.text
    from app.stress.schemas import SimulationResult
    result = SimulationResult.model_validate(sim.json()['data'])
    decision = client.post('/api/v1/decisions', json=decision_input(snapshot, result).model_dump(mode='json'))
    assert decision.status_code == 200, decision.text
    assert decision.json()['data']['verdict'] == 'STAY'
    assert isinstance(decision.json()['data']['stay_value_usd'], str)
    assert any('SYNTHETIC' in w for w in decision.json()['warnings'])
    assert {'/api/v1/graph/{strategy_id}', '/api/v1/simulations', '/api/v1/decisions', '/api/v1/markets'} <= set(client.get('/openapi.json').json()['paths'])


def test_api_invalid_and_unknown(snapshot):
    client = TestClient(create_app(snapshot))
    assert client.get('/api/v1/graph/unknown').status_code == 404
    data = request(snapshot).model_dump(mode='json')
    data['parameters']['oracle_price_change_ratio'] = '-2'
    assert client.post('/api/v1/simulations', json=data).status_code == 422
    data['scenario_type'] = 'INVENTED'
    assert client.post('/api/v1/simulations', json=data).status_code == 422
    data = request(snapshot).model_dump(mode='json')
    data['scenario_scope'] = 'VERIFIED_TARGET'
    assert client.post('/api/v1/simulations', json=data).status_code == 422


def real_shaped_market(snapshot):
    """Only for unit tests: pretend RPC payload shape, NOT actual chain evidence."""
    data = snapshot.model_dump(mode='json')
    for m in data['markets']:
        m['data_mode'] = 'REAL'
    market = data['markets'][0]
    market['block_number'] = 123456
    data['evidence'][0].update(data_mode='REAL', source_kind='RPC', status='OK',
                               target_address=market['morpho']['morpho_address'], block_number=123456)
    market['verification'] = [dict(check='morpho.oracle', onchain_value=market['morpho']['oracle_address'],
                                    status='VERIFIED', evidence_ids=market['evidence_ids'])]
    return data


def test_confirmed_relationship_requires_matching_block_and_target(snapshot):
    data = real_shaped_market(snapshot)
    snap = AnalyticsSnapshot.model_validate(data)
    get = lambda s: next(e for e in build_graph(s, s.strategies[0].strategy_id).edges if e.relation_type == 'USES_ORACLE')
    assert get(snap).verification_status == 'CONFIRMED'
    data['evidence'][0]['block_number'] = 123455
    assert get(AnalyticsSnapshot.model_validate(data)).verification_status != 'CONFIRMED'
    data['evidence'][0]['block_number'] = 123456
    data['evidence'][0]['target_address'] = '0x'+'9'*40
    assert get(AnalyticsSnapshot.model_validate(data)).verification_status != 'CONFIRMED'


def test_missing_rpc_record_cannot_confirm(snapshot):
    data = real_shaped_market(snapshot)
    data['evidence'] = []
    snap = AnalyticsSnapshot.model_validate(data)
    assert not any(e.verification_status == 'CONFIRMED' for e in build_graph(snap, snap.strategies[0].strategy_id).edges)


def test_shared_oracle_two_positions_unique_exposure(snapshot):
    data = snapshot.model_dump(mode='json')
    second = dict(data['positions'][0], position_id='pos_second_fund', collateral_amount='1000000', borrow_amount='600000')
    data['positions'].append(second)
    snap = AnalyticsSnapshot.model_validate(data)
    result = AnalyticsService(snap).simulate(request(snap))
    assert set(result.affected_position_ids) == {'pos_demo_fund', 'pos_second_fund'}
    assert result.potentially_exposed_capital_usd == Decimal('1200000')


def test_unknown_usd_price_does_not_prevent_token_based_health(snapshot):
    data = snapshot.model_dump(mode='json')
    data['markets'][0]['morpho']['loan_price'] = None
    snap = AnalyticsSnapshot.model_validate(data)
    value = value_position(snap, snap.positions[0])
    assert value.equity_usd is None
    assert value.ltv_ratio == Decimal('0.6')
    assert exposure(snap, ['pos_demo_fund']) is None


def test_non_dollar_loan_price_does_not_change_oracle_units(snapshot):
    data = snapshot.model_dump(mode='json')
    data['markets'][0]['morpho']['loan_price']['value'] = '2'
    snap = AnalyticsSnapshot.model_validate(data)
    value = value_position(snap, snap.positions[0])
    assert value.debt_value_usd == Decimal('2400000')
    assert value.ltv_ratio == Decimal('0.6')


def test_excluded_unquantified_scenario_is_visible(snapshot):
    service = AnalyticsService(snapshot)
    a = service.simulate(request(snapshot))
    b = service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING'))
    assumptions = [dict(loss_scenario_ref=a.simulation_id, probability_over_horizon='0.01', include_in_financial_comparison=False),
                   dict(loss_scenario_ref=b.simulation_id, probability_over_horizon='0.02')]
    result = service.decide(decision_input(snapshot, b, scenario_assumptions=assumptions))
    assert result.verdict == 'STAY'
    assert result.excluded_unquantified_scenario_ids == (a.simulation_id,)
    assert 'UNQUANTIFIED_RISK_NOT_INCLUDED' in result.warnings


def test_missing_carry_prevents_verdict(snapshot):
    service = AnalyticsService(snapshot)
    b = service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING'))
    result = service.decide(decision_input(snapshot, b, estimated_net_carry_usd=None))
    assert result.decision_status == 'INSUFFICIENT_DATA'
    assert result.verdict is None


def test_api_quote_unavailable_is_explicit_business_status(snapshot):
    client = TestClient(create_app(snapshot))
    sim = client.post('/api/v1/simulations', json=request(snapshot, 'UNAUTHORIZED_MINTING').model_dump(mode='json'))
    from app.stress.schemas import SimulationResult
    result = SimulationResult.model_validate(sim.json()['data'])
    response = client.post('/api/v1/decisions', json=decision_input(snapshot, result, exit_quote_id=None).model_dump(mode='json'))
    assert response.status_code == 200
    assert response.json()['status'] == 'QUOTE_UNAVAILABLE'
    assert 'exit_cost_usd' in response.json()['missing_fields']


def test_probabilities_sum_over_one_rejected(snapshot):
    service = AnalyticsService(snapshot)
    a = service.simulate(request(snapshot))
    b = service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING'))
    with pytest.raises(ValidationError):
        decision_input(snapshot, b, scenarios_mutually_exclusive=True, scenario_assumptions=[dict(loss_scenario_ref=r.simulation_id, probability_over_horizon='0.6') for r in (a, b)])


def test_decision_uses_position_loss_not_fund_aggregate(snapshot):
    data = snapshot.model_dump(mode='json')
    data['positions'].append(dict(data['positions'][0], position_id='pos_second_fund'))
    snap = AnalyticsSnapshot.model_validate(data)
    service = AnalyticsService(snap)
    sim = service.simulate(request(snap, 'UNAUTHORIZED_MINTING'))
    assert sim.scenario_estimated_loss_usd == Decimal('800000')
    result = service.decide(decision_input(snap, sim))
    assert result.expected_loss_usd == Decimal('8000')


def test_normalizer_oracle_decimals_contract(snapshot):
    # Existing Data V1 normalizer must yield exactly the oracle units analytics uses.
    from app.ingestion.normalize import OracleRead, SourceRef, oracle_price
    p = snapshot.positions[0]
    ref = SourceRef(source_id='fixture:analytics', evidence_id=snapshot.evidence[0].evidence_id, observed_at=snapshot.as_of)
    read = OracleRead(oracle_address=snapshot.market(p).morpho.oracle_address, price_raw=10**24,
                      block_number=123456, ref=ref)
    price, reason = oracle_price(read, p.collateral_asset, p.loan_asset)
    assert reason is None
    assert price.value == Decimal('1')


def test_decision_non_exclusive_probabilities_exceeding_one(snapshot):
    app = create_app(snapshot)
    service = app.state.analytics
    a = service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING'))
    b = service.simulate(request(snapshot, 'UNAUTHORIZED_MINTING',
                                 parameters={'assumed_token_haircut_ratio': '0.1'}))
    req = decision_input(snapshot, a, scenarios_mutually_exclusive=False,
                         scenario_assumptions=[dict(loss_scenario_ref=r.simulation_id,
                                                    probability_over_horizon='0.6') for r in (a, b)])
    response = TestClient(app).post('/api/v1/decisions', json=req.model_dump(mode='json'))
    assert response.status_code == 200, response.text
    result = response.json()['data']
    assert result['decision_status'] == 'INSUFFICIENT_DATA'
    assert 'OVERLAPPING_SCENARIOS_NOT_AGGREGATED' in result['warnings']
    assert result['expected_loss_usd'] is None
    assert result['stay_value_usd'] is None
    assert result['verdict'] is None


def test_stress_minting_haircut_on_underlying_dependency(snapshot):
    underlying = Node(id='asset:1:0x' + '9'*40, type='ASSET', label='Hypothetical underlying')
    sy = snapshot.markets[1].pendle.sy.asset_id
    edge = Edge(id='hypothesis:underlying-backing', source=sy, target=underlying.id,
                relation_type='BACKED_BY', verification_status='HYPOTHESIS',
                failure_modes=('ECONOMIC',), as_of=snapshot.as_of)
    snap = revalidate(snapshot, additional_nodes=[underlying.model_dump(mode='json')],
                      additional_edges=[edge.model_dump(mode='json')])
    result = AnalyticsService(snap).simulate(request(snap, 'UNAUTHORIZED_MINTING',
        target_node_id=underlying.id, scenario_scope='HYPOTHETICAL',
        parameters={'assumed_token_haircut_ratio': '0.2'}))
    assert result.affected_position_ids == ('pos_demo_fund',)
    assert result.potentially_exposed_capital_usd == Decimal('800000')
    assert result.scenario_estimated_loss_usd is None
    effect = result.position_effects['pos_demo_fund']
    assert effect.scenario_estimated_loss_usd is None
    assert effect.after == effect.before
    assert 'HAIRCUT_IGNORED_TARGET_NOT_COLLATERAL; propagation of price loss is unquantified' in result.warnings
