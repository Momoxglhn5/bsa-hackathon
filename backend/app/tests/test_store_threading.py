"""Regression: concurrent Data requests failed with sqlite3.ProgrammingError (HTTP 500).

FastAPI runs a sync generator dependency and the endpoint on threadpool workers that
may differ; the per-request connection must survive that hop.
"""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from app.api.routes_data import get_store
from app.models.evidence import DataMode


def test_request_store_survives_threadpool_hops(tmp_path):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_path=tmp_path / 'radar.db')))
    dependency = get_store(request)
    # Both workers stay alive, so the endpoint really runs on another thread id.
    with ThreadPoolExecutor(1) as setup, ThreadPoolExecutor(1) as endpoint:
        store = setup.submit(next, dependency).result()
        assert endpoint.submit(store.list_strategies, data_mode=DataMode.REAL).result() == []
        endpoint.submit(dependency.close).result()
