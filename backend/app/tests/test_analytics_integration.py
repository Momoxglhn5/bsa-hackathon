"""Core/Data integration and regression tests. Every fixture is synthetic."""
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.analytics.dataset import AnalyticsSnapshot, load_snapshot
from app.analytics.provider import snapshot_from_store
from app.analytics.service import AnalyticsService
from app.analytics.valuation import value_position
from app.decision.forecast import ForecastBundle
from app.graph.builder import build_graph
from app.main import DEFAULT_SNAPSHOT, create_app
from app.models.evidence import DataMode, RelationStatus, RelationType, make_relation
from app.models.timeseries import TimeseriesDTO
from app.risk.scoring import current_risk
from app.storage.parquet_store import ParquetStore
from app.storage.sqlite_store import SqliteStore
from app.tests.test_core_analytics import decision_input, real_shaped_market, request


@pytest.fixture
def snapshot():
    return load_snapshot(DEFAULT_SNAPSHOT)


def validate(data):
    return AnalyticsSnapshot.model_validate(data)


def seed(snapshot, db):
    with SqliteStore(db) as store:
        store.save_markets(snapshot.markets)
        store.save_strategies(snapshot.strategies)
        store.save_positions(snapshot.positions)
        store.save_evidence(snapshot.evidence)
        store.save_relations(snapshot.relations)


def test_missing_market_block_never_confirms(snapshot):
    data = real_shaped_market(snapshot)
    data['markets'][0]['block_number'] = None
    graph = build_graph(validate(data), snapshot.strategies[0].strategy_id)
    assert not any(e.verification_status == 'CONFIRMED' for e in graph.edges)


@pytest.mark.parametrize('age', [timedelta(days=365), timedelta(seconds=-1)])
def test_bad_reference_price_date_blocks_decision(snapshot, age):
    data = snapshot.model_dump(mode='json')
    data['markets'][0]['morpho']['collateral_price']['observed_at'] = (snapshot.as_of-age).isoformat()
    snap = validate(data)
    service = AnalyticsService(snap)
    result = service.simulate(request(snap, 'UNAUTHORIZED_MINTING'))
    decision = service.decide(decision_input(snap, result))
    assert decision.verdict is None
    assert decision.equity_now_usd is None
    assert decision.quote_status == 'INSUFFICIENT_DATA'


def test_live_valuation_ages_without_mutating_snapshot(snapshot):
    assert value_position(snapshot, snapshot.positions[0]).equity_usd == Decimal('800000')
    value = value_position(snapshot, snapshot.positions[0], now=snapshot.as_of+timedelta(minutes=6))
    assert value.equity_usd is None and value.health_factor is None


def test_data_and_core_routers_coexist_with_mode_isolation(snapshot, tmp_path):
    db = tmp_path/'data.sqlite'
    seed(snapshot, db)
    client = TestClient(create_app(db, tmp_path/'series', clock=lambda: snapshot.as_of))
    sid = snapshot.strategies[0].strategy_id
    assert client.get('/api/v1/markets?data_mode=DEMO').status_code == 200
    assert client.get(f'/api/v1/graph/{sid}').status_code == 503
    response = client.get(f'/api/v1/graph/{sid}?data_mode=DEMO')
    assert response.status_code == 200, response.text
    assert response.json()['provenance']['data_modes'] == ['DEMO']
    sim = client.post('/api/v1/simulations?data_mode=DEMO', json=request(snapshot, 'UNAUTHORIZED_MINTING').model_dump(mode='json'))
    assert sim.status_code == 200, sim.text
    from app.stress.schemas import SimulationResult
    result = SimulationResult.model_validate(sim.json()['data'])
    decision = client.post('/api/v1/decisions?data_mode=DEMO', json=decision_input(snapshot, result).model_dump(mode='json'))
    assert decision.status_code == 200, decision.text
    # The Data store has no execution quote; spot liquidity cannot replace it.
    assert decision.json()['data']['quote_status'] == 'QUOTE_UNAVAILABLE'
    assert decision.json()['data']['verdict'] is None


def test_refresh_invalidates_old_simulation(snapshot, tmp_path):
    db = tmp_path/'data.sqlite'
    seed(snapshot, db)
    client = TestClient(create_app(db, tmp_path/'series', clock=lambda: snapshot.as_of))
    from app.stress.schemas import SimulationResult
    response = client.post('/api/v1/simulations?data_mode=DEMO', json=request(snapshot, 'UNAUTHORIZED_MINTING').model_dump(mode='json'))
    result = SimulationResult.model_validate(response.json()['data'])
    with SqliteStore(db) as store:
        store.save_positions([snapshot.positions[0].model_copy(update={'borrow_amount': Decimal('1000000'), 'borrow_amount_raw': None})])
    response = client.post('/api/v1/decisions?data_mode=DEMO', json=decision_input(snapshot, result).model_dump(mode='json'))
    assert response.status_code == 404


def test_data_json_fixture_consumed_directly():
    fixture = Path(DEFAULT_SNAPSHOT).with_name('ui_mock_data_v1.json')
    snap = load_snapshot(fixture)
    graph = build_graph(snap, snap.strategies[0].strategy_id)
    assert any(e.relation_type == 'USES_YIELD_TOKEN' for e in graph.edges)
    assert any(e.relation_type == 'ACCOUNTED_IN' and not e.required for e in graph.edges)
    assert not any(e.relation_type == 'BACKED_BY' and e.verification_status == 'CONFIRMED' for e in graph.edges)
    assert snap.positions[0].kind == 'SIMULATED'


def test_relation_proof_binds_target_and_block(snapshot):
    data = real_shaped_market(snapshot)
    snap = validate(data)
    market = snap.markets[0]
    relation = make_relation(source_node_id=market.market_key,
        target_node_id=f'contract:1:{market.morpho.oracle_address}',
        relation_type=RelationType.MARKET_ORACLE, verification_status=RelationStatus.ONCHAIN_VERIFIED,
        evidence=list(snap.evidence), observed_at=snap.as_of)
    data['relations'] = [relation.model_dump(mode='json')]
    graph = build_graph(validate(data), snap.strategies[0].strategy_id)
    edge = next(e for e in graph.edges if e.relation_type == 'USES_ORACLE')
    assert edge.id == relation.relation_id and edge.verification_status == 'CONFIRMED'
    data['evidence'][0]['target_address'] = '0x'+'9'*40
    graph = build_graph(validate(data), snap.strategies[0].strategy_id)
    assert next(e for e in graph.edges if e.relation_type == 'USES_ORACLE').verification_status == 'UNRESOLVED'


@pytest.mark.parametrize('forecast_state,expected', [('absent','FORECAST_UNAVAILABLE'), ('stale','STALE'), ('unvalidated','NOT_VALIDATED'), ('valid',None)])
def test_forecast_cache_and_fallback_values(snapshot, forecast_state, expected):
    data = snapshot.model_dump(mode='json')
    if forecast_state != 'absent':
        data['forecasts'] = [ForecastBundle(bundle_id='forecast:test', strategy_id=snapshot.strategies[0].strategy_id,
            position_id=snapshot.positions[0].position_id, horizon_days=7,
            generated_at=snapshot.as_of-timedelta(hours=1),
            valid_until=snapshot.as_of+timedelta(hours=1) if forecast_state != 'stale' else snapshot.as_of-timedelta(minutes=1),
            validation_status='NOT_VALIDATED' if forecast_state == 'unvalidated' else 'VALIDATED',
            transformation='NET_CARRY_USD_OVER_HORIZON', estimated_net_carry_usd=Decimal('2000'),
            methodology='Synthetic test projection; not a fitted model', backtest_reference='synthetic-test-only',
            evidence_ids=(snapshot.evidence[0].evidence_id,)).model_dump(mode='json')]
    snap = validate(data)
    service = AnalyticsService(snap)
    sim = service.simulate(request(snap, 'UNAUTHORIZED_MINTING'))
    normal = service.decide(decision_input(snap, sim))
    predictive = service.decide(decision_input(snap, sim, mode='predictive'))
    assert predictive.fallback_reason == expected
    if expected:
        assert predictive.effective_mode == 'normal'
        for attr in ('verdict', 'stay_value_usd', 'exit_value_usd', 'expected_loss_usd', 'break_even_probability'):
            assert getattr(predictive, attr) == getattr(normal, attr)
    else:
        assert predictive.effective_mode == 'predictive'
        assert predictive.forecast_bundle_id == 'forecast:test'
        assert predictive.stay_value_usd == normal.stay_value_usd+Decimal('1000')
        assert predictive.expected_loss_usd == normal.expected_loss_usd


def test_complete_daily_liquidity_history_and_sparse_rejection(snapshot):
    data = snapshot.model_dump(mode='json')
    points = [dict(timestamp=(snapshot.as_of-timedelta(days=i)).isoformat(), value='80' if i==0 else '100',
                   status='OK', evidence_ids=[snapshot.evidence[0].evidence_id]) for i in range(30,-1,-1)]
    data['series'] = [dict(chain_id=1, data_mode='DEMO', series_id='ts_test_liquidity',
        metric='LIQUIDITY_USD', subject_kind='MARKET', subject_id=snapshot.markets[0].market_id,
        unit='USD', source_id='fixture:analytics', status='OK', points=points)]
    risk = current_risk(validate(data), snapshot.strategies[0].strategy_id, snapshot.as_of)
    factor = next(f for f in risk.factor_explanations if f.factor_id == 'L_TVL_DRAWDOWN')
    assert factor.risk == Decimal('0.2')
    data['series'][0]['points'] = [points[0], points[-1]]
    risk = current_risk(validate(data), snapshot.strategies[0].strategy_id, snapshot.as_of)
    assert 'L_TVL_DRAWDOWN' in risk.unknown_factors


def test_exported_store_snapshot_roundtrip_and_simulated_position(snapshot, tmp_path):
    import importlib.util
    script = Path(DEFAULT_SNAPSHOT).parents[2]/'scripts'/'export_analytics_snapshot.py'
    spec = importlib.util.spec_from_file_location('core_export', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = tmp_path/'data.sqlite'
    seed(snapshot, db)
    with SqliteStore(db) as store:
        exported = snapshot_from_store(store, ParquetStore(tmp_path/'series'), DataMode.DEMO)
        before = store.counts()
        augmented = module.with_simulated_position(exported, snapshot.strategies[0].strategy_id,
                                                   Decimal('5000000'), Decimal('3000000'))
        assert store.counts() == before
    assert augmented.positions[-1].kind == 'SIMULATED'
    assert not augmented.quotes
    assert validate(json.loads(augmented.model_dump_json())) == augmented


@pytest.mark.parametrize('amount', ['1000000','5000000'])
def test_quote_for_other_size_never_scaled(snapshot, amount):
    data = snapshot.model_dump(mode='json')
    data['positions'][0].update(collateral_amount=amount, collateral_amount_raw=None)
    snap = validate(data)
    service = AnalyticsService(snap)
    sim = service.simulate(request(snap, 'UNAUTHORIZED_MINTING'))
    result = service.decide(decision_input(snap, sim))
    assert result.quote_status == 'QUOTE_UNAVAILABLE' and result.verdict is None


def test_catalog_available_without_stored_market(tmp_path, snapshot):
    client = TestClient(create_app(tmp_path/'empty.sqlite', tmp_path/'series', clock=lambda: snapshot.as_of))
    assert client.get('/api/v1/scenarios').status_code == 200
    assert client.get('/api/v1/incident-frequencies').json()['status'] == 'INSUFFICIENT_DATA'
    assert client.get('/api/v1/graph/'+snapshot.strategies[0].strategy_id).status_code == 503


def test_real_shaped_store_keeps_simulated_fund_potential(snapshot, tmp_path):
    data = real_shaped_market(snapshot)
    data['positions'][0]['data_mode'] = 'REAL'
    data['strategies'][0]['data_mode'] = 'REAL'
    snap = validate(data)
    db = tmp_path/'test-only-real-shape.sqlite'
    seed(snap, db)
    client = TestClient(create_app(db, tmp_path/'series', clock=lambda: snap.as_of))
    response = client.post('/api/v1/simulations', json=request(snap, scenario_scope='VERIFIED_TARGET').model_dump(mode='json'))
    assert response.status_code == 200, response.text
    result = response.json()['data']
    assert result['confirmed_exposed_capital_usd'] == '0'
    assert Decimal(result['potentially_exposed_capital_usd']) == Decimal('800000')
    assert result['position_effects']['pos_demo_fund']['after']['liquidatable'] is True


def test_cross_chain_explicit_haircut_requires_mapping_assumption(snapshot):
    from app.graph.types import Edge, Node
    data = snapshot.model_dump(mode='json')
    bridge = 'bridge:test-only'
    data['additional_nodes'] = [Node(id=bridge, type='BRIDGE', label='Hypothetical bridge').model_dump(mode='json')]
    data['additional_edges'] = [Edge(id='edge:bridge-test', source=snapshot.positions[0].collateral_asset.asset_id,
        target=bridge, relation_type='BRIDGED_BY', verification_status='HYPOTHESIS',
        failure_modes=('LIVENESS',), as_of=snapshot.as_of).model_dump(mode='json')]
    snap = validate(data)
    service = AnalyticsService(snap)
    unmapped = request(snap, 'CROSS_CHAIN_FAILURE', target_node_id=bridge,
        parameters={'assumed_token_haircut_ratio':'0.2', 'outage_duration_hours':'24'})
    assert service.simulate(unmapped).scenario_estimated_loss_usd is None
    mapped = unmapped.model_copy(update={'parameters':unmapped.parameters.model_copy(update={'haircut_applies_to_affected_collateral':True})})
    result = service.simulate(mapped)
    assert result.scenario_estimated_loss_usd == Decimal('400000')
    assert result.operationally_blocked_usd == Decimal('800000')
    assert result.position_effects['pos_demo_fund'].after.health_factor == Decimal('1.525')


def test_fixed_risk_policy_cannot_accept_arbitrary_weights(snapshot):
    from app.risk.scoring import Factor
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Factor(factor_id='L_UTILIZATION', dimension='Liquidity', weight=Decimal('0.9'), explanation='invalid policy')


def test_store_with_explicit_quote_context_flips_conditional_decision(snapshot, tmp_path, monkeypatch):
    inputs = tmp_path/'inputs.json'
    inputs.write_text(json.dumps({'quotes':[q.model_dump(mode='json') for q in snapshot.quotes]}))
    monkeypatch.setenv('ANALYTICS_INPUTS', str(inputs))
    db = tmp_path/'data.sqlite'
    seed(snapshot, db)
    client = TestClient(create_app(db, tmp_path/'series', clock=lambda:snapshot.as_of))
    from app.stress.schemas import SimulationResult
    response = client.post('/api/v1/simulations?data_mode=DEMO', json=request(snapshot, 'UNAUTHORIZED_MINTING').model_dump(mode='json'))
    sim = SimulationResult.model_validate(response.json()['data'])
    payload = decision_input(snapshot, sim).model_dump(mode='json')
    response = client.post('/api/v1/decisions?data_mode=DEMO', json=payload)
    assert response.status_code == 200, response.text
    assert response.json()['data']['verdict'] == 'STAY'
    payload['scenario_assumptions'][0]['probability_over_horizon'] = '0.1'
    response = client.post('/api/v1/decisions?data_mode=DEMO', json=payload)
    assert response.json()['data']['verdict'] == 'EXIT'
    assert Decimal(response.json()['data']['break_even_probability']) == Decimal('0.05275')


def test_demo_evidence_cannot_value_real_market(snapshot):
    data = snapshot.model_dump(mode='json')
    for m in data['markets']:
        m['data_mode'] = 'REAL'
    snap = validate(data)
    assert value_position(snap, snap.positions[0]).equity_usd is None


def test_partial_rpc_record_cannot_confirm(snapshot):
    data = real_shaped_market(snapshot)
    data['evidence'][0]['status'] = 'PARTIAL'
    graph = build_graph(validate(data), snapshot.strategies[0].strategy_id)
    assert not any(e.verification_status == 'CONFIRMED' for e in graph.edges)


def test_indirect_failure_of_healthy_fallback_revisits_blocked_edge(snapshot):
    from app.graph.propagation import propagate
    from app.graph.types import Edge, Fallback, GraphDTO, Node
    data = real_shaped_market(snapshot)
    snap = validate(data)
    ev = snap.evidence[0].evidence_id
    nodes = tuple(Node(id=id, type='POSITION' if id.startswith('pos_') else 'ORACLE', label=id)
                  for id in ('pos_demo_fund','a_primary','backup','z_failure'))
    edges = (Edge(id='primary', source='pos_demo_fund', target='a_primary', relation_type='USES_ORACLE',
                  verification_status='CONFIRMED', as_of=snap.as_of,
                  fallbacks=(Fallback(node_id='backup', state='HEALTHY', evidence_ids=(ev,)),)),
             Edge(id='backup-failure', source='backup', target='z_failure', relation_type='USES_ORACLE',
                  verification_status='CONFIRMED', as_of=snap.as_of))
    graph = GraphDTO(strategy_id=snap.strategies[0].strategy_id, snapshot_as_of=snap.as_of, nodes=nodes, edges=edges)
    assert not propagate(snap, graph, ['a_primary']).confirmed_position_ids
    impact = propagate(snap, graph, ['a_primary','z_failure'])
    assert impact.confirmed_position_ids == ('pos_demo_fund',)
    assert impact.representative_paths['pos_demo_fund'] == ('pos_demo_fund','a_primary')
    stale = propagate(snap, graph, ['a_primary'], now=snap.as_of+timedelta(minutes=6))
    assert stale.potential_position_ids == ('pos_demo_fund',)
