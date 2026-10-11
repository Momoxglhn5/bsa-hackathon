"""Verify the jury replay's financial boundaries on archived real inputs."""
import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import pytest
from app.analytics.dataset import load_snapshot
from app.models.evidence import DataMode

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('radar_demo', ROOT/'backend/scripts/build_radar_demo.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)
SOURCE = ROOT/'docs/demo/radar/source'


@pytest.fixture
def replay(monkeypatch):
    import httpx
    def no_network(*args, **kwargs):
        raise AssertionError('jury replay must not call providers')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', no_network)
    snapshot = load_snapshot(SOURCE/'analytics_snapshot.json')
    quotes = json.loads((SOURCE/'exit_quotes.json').read_text())['records']
    original = snapshot.model_dump_json()
    result = demo.build(snapshot, quotes)
    assert snapshot.model_dump_json() == original
    return result


def test_demo_does_not_aggregate_alternative_funds(replay):
    assert Decimal(replay['valuation']['equity_usd']).quantize(Decimal('0.01')) == 600000
    for shock in ('10', '20', '40'):
        stress = replay['cases'][shock]['observed']['stress']
        # A single 2M collateral position, not the sum of the 1/2/5M alternatives.
        assert Decimal(stress['scenario_estimated_loss_usd']).quantize(Decimal('0.01')) == Decimal(2000000)*Decimal(shock)/100


def test_observed_closing_is_not_promoted_by_pedagogical_assumptions(replay):
    for mode in replay['cases']['20'].values():
        for decision in mode['decisions'].values():
            assert decision['horizon_days'] == 7
    for case in replay['cases'].values():
        assert all(d['verdict'] is None for d in case['observed']['decisions'].values())
    conditional = replay['cases']['20']['conditional']['decisions']
    assert conditional['2']['verdict'] == 'STAY'
    assert conditional['10']['verdict'] == 'EXIT'
    assert replay['closing_assumptions']['basis'] == 'USER_ASSUMPTION'
    assert any(e['source_id'] == 'demo_closing_assumption' and e['source_kind'] == 'USER_INPUT'
               for e in replay['evidence'])


def test_replay_retains_unavailable_route_and_all_referenced_proofs(replay):
    records = replay['quotes']
    assert len(records) == 3
    unavailable = next(r for r in records if Decimal(r['size_usd']) == 5000000)
    assert unavailable['quote'] is None
    assert 'MarketProportionTooHigh' in unavailable['reason']
    evidence = {e['evidence_id'] for e in replay['evidence']}
    for record in records:
        ids = record['quote']['evidence_ids'] if record['quote'] else record['evidence_ids']
        assert set(ids) <= evidence
    assert all(set(edge['evidence_ids']) <= evidence for edge in replay['graph']['edges'])


def test_builder_rejects_demo_market_labeled_as_real():
    snapshot = load_snapshot(SOURCE/'analytics_snapshot.json')
    changed = snapshot.model_copy(update={'markets': tuple(
        m.model_copy(update={'data_mode': DataMode.DEMO}) for m in snapshot.markets)})
    with pytest.raises(ValueError, match='real market'):
        demo.build(changed, [])
