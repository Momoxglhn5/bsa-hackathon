"""Core catalog: ids come from the served dataset, never reconstructed by the client."""
from fastapi.testclient import TestClient

from app.analytics.dataset import load_snapshot
from app.main import DEFAULT_SNAPSHOT, create_app


def test_catalog_lists_loaded_objects_and_quote_contexts():
    snapshot = load_snapshot(DEFAULT_SNAPSHOT)
    client = TestClient(create_app(snapshot))
    response = client.get('/api/v1/catalog')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['provenance']['snapshot_id'] == snapshot.snapshot_id
    data = body['data']
    assert [p['position_id'] for p in data['positions']] == [p.position_id for p in snapshot.positions]
    assert [s['strategy_id'] for s in data['strategies']] == [s.strategy_id for s in snapshot.strategies]
    assert {q['quote_id'] for q in data['quotes']} == {q.quote_id for q in snapshot.quotes}
    # Decimals stay strings, as in every other Core envelope.
    assert isinstance(data['positions'][0]['collateral_amount'], str)
    # Every listed id is accepted by the engines.
    p = data['positions'][0]
    assert client.get(f"/api/v1/risk/{p['strategy_id']}").status_code == 200
    quote = next(q for q in data['quotes'] if q['position_id'] == p['position_id'])
    plan = client.post('/api/v1/closing-plans', json={'strategy_id': p['strategy_id'], 'position_id': p['position_id'],
                                                      'exit_quote_id': quote['quote_id']})
    assert plan.status_code == 200, plan.text
    assert plan.json()['data']['quote_id'] == quote['quote_id']


def test_catalog_without_dataset_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv('ANALYTICS_SNAPSHOT', raising=False)
    client = TestClient(create_app(tmp_path / 'empty.db', tmp_path / 'timeseries'))
    response = client.get('/api/v1/catalog')
    assert response.status_code == 503
    assert response.json()['error'] == 'unavailable'
