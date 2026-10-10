"""Freshness, provider failures and scheduling in app.ingestion.refresh (no network)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.adapters.ethereum_rpc import RpcUnreachableError
from app.adapters.morpho_api import MorphoHTTPError, MorphoTimeoutError
from app.adapters.pendle_api import PendleHTTPError, PendleRateLimitError, PendleTimeoutError
from app.ingestion.normalize import SourceRef, normalize_morpho_market
from app.ingestion.refresh import (
    TASK_INTERVALS,
    Refresher,
    Target,
    TaskResult,
    apply_maturity,
    freshness,
)
from app.models.evidence import DataMode, DataStatus
from app.models.market import TokenRef
from app.models.strategy import StrategyDTO, StrategyStatus, make_strategy_id
from app.storage.parquet_store import ParquetStore
from app.storage.sqlite_store import SqliteStore

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
MARKET_ID = "0x" + "ab" * 32
PENDLE = "0x" + "33" * 20
PT = "0x" + "11" * 20
LOAN = "0x" + "22" * 20
MORPHO_KEY = f"morpho:1:{MARKET_ID}"


def morpho_market(as_of):
    item = {"market_id": MARKET_ID, "chain_id": 1, "loan_token": LOAN, "collateral_token": PT,
            "oracle_address": "0x" + "44" * 20, "irm_address": "0x" + "55" * 20,
            "lltv_wad": "860000000000000000"}
    return normalize_morpho_market(item, SourceRef("morpho_api", "ev_" + "a" * 24, as_of)).dto


def strategy(expiry, status=StrategyStatus.CANDIDATE):
    return StrategyDTO(
        chain_id=1, data_mode=DataMode.REAL, strategy_id=make_strategy_id(1, MARKET_ID, PT),
        status=status, status_reason="address match", data_status=DataStatus.UNVERIFIED,
        morpho_market_id=MARKET_ID, pendle_market_address=PENDLE,
        pt=TokenRef(chain_id=1, address=PT), loan_asset=TokenRef(chain_id=1, address=LOAN),
        expiry=expiry, as_of=NOW - timedelta(days=1), source_ids=("morpho_api",),
    )


class Failing:
    """Fake API client whose every call raises `exc`."""

    def __init__(self, exc):
        self.exc = exc

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise self.exc
        return fail


def unreachable_rpc():
    raise RpcUnreachableError("RPC unreachable (host=fake)")


@pytest.fixture
def stores(tmp_path):
    with SqliteStore(tmp_path / "db.sqlite") as sqlite:
        yield sqlite, ParquetStore(tmp_path / "ts")


def refresher(stores, morpho_exc, pendle_exc, now=NOW):
    sqlite, parquet = stores
    return Refresher(
        morpho=Failing(morpho_exc), pendle=Failing(pendle_exc), sqlite=sqlite, parquet=parquet,
        rpc_factory=unreachable_rpc, target=Target(MARKET_ID, PENDLE), clock=lambda: now,
    )


def test_freshness_age_and_staleness():
    stale = freshness(NOW - timedelta(seconds=400), "current", NOW)
    assert stale.is_stale and stale.age_seconds == 400 and stale.ttl_seconds == 300
    fresh = freshness(NOW - timedelta(seconds=299), "current", NOW)
    assert not fresh.is_stale
    assert not freshness(NOW - timedelta(hours=23), "verification", NOW).is_stale
    missing = freshness(None, "current", NOW)
    assert missing.is_stale and missing.age_seconds is None


def test_provider_failure_returns_cached_stale(stores):
    cached = morpho_market(NOW - timedelta(minutes=10))
    stores[0].save_markets([cached])
    r = refresher(stores, MorphoHTTPError("boom", status_code=500), PendleHTTPError("boom", status_code=500))
    result = r.refresh_current()
    assert not result.ok and result.errors
    assert result.written["markets"] == 0
    dto, fresh = r.get_with_freshness("market", MORPHO_KEY)
    assert dto == cached  # served unchanged, never relabelled
    assert fresh.is_stale and fresh.age_seconds == 600


def test_provider_failure_without_cache(stores):
    r = refresher(stores, MorphoTimeoutError("timeout"), PendleTimeoutError("timeout"))
    result = r.refresh_current()
    assert not result.ok
    dto, fresh = r.get_with_freshness("market", MORPHO_KEY)
    assert dto is None and fresh.is_stale and fresh.as_of is None


@pytest.mark.parametrize("morpho_exc, pendle_exc", [
    (MorphoHTTPError("rate limited", status_code=429), PendleRateLimitError("rate limited", status_code=429)),
    (MorphoTimeoutError("timeout"), PendleTimeoutError("timeout")),
    (MorphoHTTPError("server error", status_code=500), PendleHTTPError("server error", status_code=500)),
])
def test_http_errors_recorded_no_zero_values(stores, morpho_exc, pendle_exc, tmp_path):
    r = refresher(stores, morpho_exc, pendle_exc)
    for result in (r.refresh_current(), r.refresh_metadata(), r.backfill_history(days=7)):
        assert isinstance(result, TaskResult) and not result.ok and result.errors
    assert stores[0].counts()["markets"] == 0
    assert stores[0].counts()["strategies"] == 0
    assert not list((tmp_path / "ts").rglob("*.parquet"))  # nothing written, so no zeros either
    joined = " ".join(r.refresh_current().errors)
    assert "rpc" in joined and "morpho" in joined and "pendle" in joined


def test_apply_maturity():
    expired = strategy(expiry=NOW - timedelta(seconds=1))
    out = apply_maturity(expired, NOW)
    assert out.status == StrategyStatus.EXPIRED and out is not expired
    assert expired.status == StrategyStatus.CANDIDATE  # input untouched

    active = strategy(expiry=NOW + timedelta(days=30))
    assert apply_maturity(active, NOW) is active


def test_run_due_only_elapsed(stores):
    calls: list[str] = []

    class Counting(Refresher):
        def tasks(self):
            def make(name):
                def task():
                    calls.append(name)
                    return TaskResult(name, True, NOW, NOW)
                return task
            return {name: make(name) for name in TASK_INTERVALS}

    r = Counting(morpho=None, pendle=None, sqlite=stores[0], parquet=stores[1], target=Target(MARKET_ID, PENDLE))
    last: dict[str, datetime] = {}
    r.run_due(NOW, last)
    assert sorted(calls) == sorted(TASK_INTERVALS)  # first run: everything

    calls.clear()
    r.run_due(NOW + timedelta(seconds=299), last)
    assert calls == []

    r.run_due(NOW + timedelta(seconds=300), last)
    assert sorted(calls) == ["apply_maturity", "refresh_current"]

    calls.clear()
    r.run_due(NOW + timedelta(seconds=1800), last)
    assert sorted(calls) == ["apply_maturity", "refresh_current", "refresh_metadata"]

    calls.clear()
    r.run_due(NOW + timedelta(days=1), last)
    assert sorted(calls) == sorted(TASK_INTERVALS)
