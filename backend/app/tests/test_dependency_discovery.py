"""Regression checks on real archived declarations, not backing assertions."""
import json
from types import SimpleNamespace

import pytest

from app.analytics.dataset import load_snapshot
from app.graph.builder import build_graph
from app.graph.exposure import dependency_exposure
from app.graph.resolver import resolve_relations
from app.models.evidence import EvidenceDTO, RelationDTO
from app.ingestion.dependencies import discover_sy_dependencies
from app.adapters.ethereum_rpc import RpcRevertError
from app.tests.test_radar_demo import demo, SOURCE


@pytest.fixture
def archive():
    return json.loads((SOURCE/'dependencies.json').read_text())


def test_archived_declarations_are_confirmed_but_not_automatic_losses(archive):
    snapshot = load_snapshot(SOURCE/'analytics_snapshot.json')
    result = demo.build(snapshot, json.loads((SOURCE/'exit_quotes.json').read_text())['records'], archive)
    declared = [e for e in result['graph']['edges'] if e['relation_type'] in
                {'ACCEPTS_INPUT', 'DECLARES_REDEMPTION_TOKEN', 'DECLARES_ASSET'}]
    assert len(declared) == 7
    assert all(e['verification_status'] == 'CONFIRMED' and not e['required'] for e in declared)
    assert all(e['transmission_note'] for e in declared)
    assert all(e['verification_status'] == 'UNRESOLVED' for e in result['graph']['edges']
               if e['relation_type'] == 'BACKED_BY')
    assert archive['warnings'] and 'asset unavailable' in archive['warnings'][0]
    assert max(v['unique_position_count'] for v in result['dependency_exposures'].values()) == 1


def test_unrelated_rpc_return_cannot_prove_declared_asset(archive):
    from datetime import datetime
    snapshot = load_snapshot(SOURCE/'analytics_snapshot.json')
    relations = tuple(RelationDTO.model_validate(r) for r in archive['relations'])
    evidence = tuple(EvidenceDTO.model_validate(e) for e in archive['evidence'])
    bad = tuple(e.model_copy(update={'decoded_excerpt': {'result': '0x'+'1'*40}})
                if e.method == 'eth_call asset()' else e for e in evidence)
    changed = snapshot.model_copy(update={'as_of': datetime.fromisoformat(archive['as_of']),
        'relations': relations, 'evidence': bad})
    _, edges = resolve_relations(changed, {})
    assert next(e for e in edges if e.relation_type == 'DECLARES_ASSET').verification_status == 'UNRESOLVED'


def test_tampered_raw_archive_rejected(archive):
    snapshot = load_snapshot(SOURCE/'analytics_snapshot.json')
    key = next(iter(archive['raw_returns']))
    archive['raw_returns'][key] = '0x00'
    with pytest.raises(ValueError, match='hash mismatch'):
        demo.build(snapshot, [], archive)


def test_parallel_paths_count_each_position_once():
    snapshot = load_snapshot(SOURCE/'analytics_snapshot.json')
    graph = build_graph(snapshot, snapshot.strategies[0].strategy_id)
    pt = snapshot.positions[0].collateral_asset.asset_id
    result = dependency_exposure(snapshot, graph, pt)
    # The reusable API returns each independent position once, even with
    # both a direct collateral edge and a path through the lending market.
    assert len(result['positions']) == len(snapshot.positions) == 3
    assert len({p['position_id'] for p in result['positions']}) == 3
    assert result['basis'] == 'STRUCTURAL_REACHABILITY_NOT_LOSS'


def test_missing_prices_keep_exposure_unknown():
    snapshot = load_snapshot(SOURCE/'analytics_snapshot.json')
    from datetime import timedelta
    graph = build_graph(snapshot, snapshot.strategies[0].strategy_id)
    result = dependency_exposure(snapshot, graph, snapshot.positions[0].collateral_asset.asset_id,
                                 now=snapshot.as_of+timedelta(days=1))
    assert result['collateral_exposure_usd'] is None


def test_optional_interfaces_and_cycles_stop_without_inventing_links(archive):
    sy = archive['sy_address']
    token = archive['yield_token']
    proof = next(EvidenceDTO.model_validate(e) for e in archive['evidence']
                 if e['method'] == 'eth_call asset()')
    calls = []
    class ReadOnlyRpc:
        def call(self, address, abi, method, args, pinned):
            calls.append((address, method))
            assert not args and abi['stateMutability'] == 'view'
            if method in ('getTokensIn', 'getTokensOut'):
                raise RpcRevertError('unsupported optional interface')
            return SimpleNamespace(value=token, evidence=proof, raw_return='0x00')
    result = discover_sy_dependencies(ReadOnlyRpc(),
        SimpleNamespace(sy_address=sy, yield_token=token, block_number=archive['block_number']),
        SimpleNamespace(number=archive['block_number']))
    assert not result.relations
    assert len(calls) == 3
    assert len(result.warnings) == 3
    assert 'cycle' in result.warnings[-1]


def test_discovery_rejects_mixed_blocks_before_any_rpc(archive):
    with pytest.raises(ValueError, match='matching pinned block'):
        discover_sy_dependencies(None, SimpleNamespace(block_number=1), SimpleNamespace(number=2))


def test_exposure_api_returns_paths_and_rejects_unknown_node():
    from fastapi.testclient import TestClient
    from app.main import create_app
    snapshot = load_snapshot(SOURCE/'analytics_snapshot.json')
    position = snapshot.positions[0]
    url = f'/api/v1/graph/{position.strategy_id}/exposure'
    with TestClient(create_app(snapshot=snapshot, replay=True)) as api:
        response = api.get(url, params={'node_id': position.collateral_asset.asset_id})
        assert response.status_code == 200
        data = response.json()['data']
        assert data['unique_position_count'] == 3
        assert all(p['path'][-1] == position.collateral_asset.asset_id for p in data['positions'])
        assert api.get(url, params={'node_id': 'unknown'}).status_code == 404
