"""Data API over a tmp SQLite + Parquet store seeded with fixture DTOs (no network)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.routes_data import DEMO_WARNING
from app.ingestion.normalize import SourceRef, normalize_morpho_market
from app.main import create_app
from app.models.evidence import (
    DataMode,
    DataStatus,
    EvidenceDTO,
    RelationStatus,
    VerificationCheckDTO,
    VerificationStatus,
    RelationType,
    SourceKind,
    make_relation,
)
from app.models.market import TokenRef
from app.models.position import PositionDTO, PositionKind
from app.models.strategy import StrategyDTO, StrategyStatus, make_strategy_id
from app.storage.parquet_store import Observation, ParquetStore
from app.storage.sqlite_store import SqliteStore

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
MARKET_ID = "0x" + "ab" * 32
STALE_MARKET_ID = "0x" + "cd" * 32
PT, LOAN = "0x" + "11" * 20, "0x" + "22" * 20
PENDLE = "0x" + "33" * 20
MARKET_KEY = f"morpho:1:{MARKET_ID}"
STRAT_ID = make_strategy_id(1, MARKET_ID, PT)
EV = "ev_" + "a" * 24
RPC_EV = "ev_" + "b" * 24
ENVELOPE_KEYS = {"schema_version", "status", "as_of", "snapshot", "data", "provenance",
                 "missing_fields", "warnings"}


def market(market_id, as_of, mode=DataMode.REAL):
    item = {"market_id": market_id, "chain_id": 1, "loan_token": LOAN, "collateral_token": PT,
            "oracle_address": "0x" + "44" * 20, "irm_address": "0x" + "55" * 20,
            "lltv_wad": "860000000000000000"}
    return normalize_morpho_market(item, SourceRef("morpho_api", EV, as_of), data_mode=mode).dto


def strategy(mode=DataMode.REAL):
    return StrategyDTO(
        chain_id=1, data_mode=mode, strategy_id=STRAT_ID, status=StrategyStatus.VERIFIED_ACTIVE,
        data_status=DataStatus.OK, morpho_market_id=MARKET_ID, pendle_market_address=PENDLE,
        pt=TokenRef(chain_id=1, address=PT), loan_asset=TokenRef(chain_id=1, address=LOAN, decimals=6),
        expiry=NOW + timedelta(days=90), as_of=NOW - timedelta(hours=1), block_number=100,
        verification=(VerificationCheckDTO(
            check="strategy.pt_is_morpho_collateral", claimed_value=PT, claimed_source_id="ethereum_rpc",
            onchain_value=PT, status=VerificationStatus.VERIFIED, evidence_ids=(RPC_EV,)),),
        source_ids=("morpho_api", "ethereum_rpc"), evidence_ids=(EV, RPC_EV),
    ) if mode == DataMode.REAL else StrategyDTO(
        chain_id=1, data_mode=mode, strategy_id=STRAT_ID, status=StrategyStatus.CANDIDATE,
        status_reason="demo", data_status=DataStatus.UNVERIFIED, morpho_market_id=MARKET_ID,
        pt=TokenRef(chain_id=1, address=PT), loan_asset=TokenRef(chain_id=1, address=LOAN),
        as_of=NOW, source_ids=("fixture",),
    )


def rpc_evidence():
    return EvidenceDTO(chain_id=1, data_mode=DataMode.REAL, evidence_id=RPC_EV, source_kind=SourceKind.RPC,
                       source_id="ethereum_rpc", method="eth_call idToMarketParams(bytes32)",
                       block_number=100, block_hash="0x" + "ee" * 32, fetched_at=NOW, status=DataStatus.OK)


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "db.sqlite", tmp_path / "ts"


@pytest.fixture
def client(paths):
    db, ts = paths
    with SqliteStore(db) as s:
        s.save_markets([market(MARKET_ID, NOW - timedelta(seconds=60)),
                        market(STALE_MARKET_ID, NOW - timedelta(hours=1))])
        s.save_strategies([strategy(), strategy(DataMode.DEMO)])
        s.save_evidence([rpc_evidence()])
        s.save_relations([make_relation(
            source_node_id=MARKET_KEY, target_node_id=f"asset:1:{PT}",
            relation_type=RelationType.MARKET_COLLATERAL_ASSET,
            verification_status=RelationStatus.ONCHAIN_VERIFIED, evidence=[rpc_evidence()], observed_at=NOW,
        )])
        s.save_positions([PositionDTO(
            chain_id=1, data_mode=DataMode.DEMO, position_id="pos_demo_1", kind=PositionKind.SIMULATED,
            market_id=MARKET_ID, collateral_asset=TokenRef(chain_id=1, address=PT, decimals=18),
            loan_asset=TokenRef(chain_id=1, address=LOAN, decimals=6),
            collateral_amount=Decimal("1000.000000000000000001"), borrow_amount=Decimal("500.5"),
            as_of=NOW, status=DataStatus.OK, source_ids=("user_input",),
        )])
    ParquetStore(ts).write_observations([
        Observation(f"asset:1:{PT}", "MARKET_PRICE", NOW - timedelta(days=i), Decimal("0.9512345678901234567"),
                    "USD", "pendle_api", NOW, pricing_method="API_SPOT")
        for i in (2, 1, 0)
    ], DataMode.REAL)
    return TestClient(create_app(db_path=db, timeseries_root=ts, clock=lambda: NOW))


def assert_no_float(value):
    if isinstance(value, dict):
        for v in value.values():
            assert_no_float(v)
    elif isinstance(value, list):
        for v in value:
            assert_no_float(v)
    else:
        assert not isinstance(value, float), value


@pytest.mark.parametrize("url", [
    "/api/v1/health",
    "/api/v1/markets",
    "/api/v1/markets?protocol=MORPHO_BLUE",
    f"/api/v1/markets/{MARKET_KEY}",
    "/api/v1/strategies",
    f"/api/v1/strategies/{STRAT_ID}",
    f"/api/v1/series?entity_id=asset:1:{PT}&metric=MARKET_PRICE",
    "/api/v1/positions/pos_demo_1?data_mode=DEMO",
    f"/api/v1/data-status?entity_id={MARKET_KEY}",
    f"/api/v1/data-status?entity_id={STRAT_ID}",
    f"/api/v1/evidence/{RPC_EV}",
])
def test_routes_ok_with_envelope(client, url):
    r = client.get(url)
    assert r.status_code == 200, r.json()
    body = r.json()
    assert set(body) == ENVELOPE_KEYS
    assert set(body["provenance"]) == {"source_ids", "evidence_ids"}
    assert "data_mode" in body["snapshot"]
    assert_no_float(body)


def test_decimals_are_strings(client):
    m = client.get(f"/api/v1/markets/{MARKET_KEY}").json()["data"]
    assert m["morpho"]["lltv"] == "0.860000000000000000" and m["morpho"]["lltv_raw"] == "860000000000000000"
    series = client.get(f"/api/v1/series?entity_id=asset:1:{PT}&metric=MARKET_PRICE").json()["data"]
    assert [p["value"] for p in series["points"]] == ["0.9512345678901234567"] * 3
    pos = client.get("/api/v1/positions/pos_demo_1?data_mode=DEMO").json()["data"]
    assert pos["collateral_amount"] == "1000.000000000000000001"


def test_strategy_includes_relations_and_block(client):
    body = client.get(f"/api/v1/strategies/{STRAT_ID}").json()
    assert body["status"] == "OK"
    assert [r["relation_type"] for r in body["data"]["relations"]] == ["MARKET_COLLATERAL_ASSET"]
    assert body["snapshot"]["block_number"] == 100
    assert RPC_EV in body["provenance"]["evidence_ids"]


def test_evidence_snapshot_has_block_hash(client):
    assert client.get(f"/api/v1/evidence/{RPC_EV}").json()["snapshot"]["block_hash"] == "0x" + "ee" * 32


@pytest.mark.parametrize("url, code", [
    (f"/api/v1/markets/morpho:1:0x{'00' * 32}", 404),
    ("/api/v1/strategies/strat_" + "0" * 24, 404),
    ("/api/v1/evidence/ev_" + "f" * 24, 404),
    ("/api/v1/positions/pos_unknown", 404),
    (f"/api/v1/series?entity_id=asset:1:{LOAN}&metric=MARKET_PRICE", 404),
    ("/api/v1/markets/morpho:1:0x1234", 400),
    ("/api/v1/markets/notanid", 400),
    ("/api/v1/strategies/not_a_strategy", 400),
    ("/api/v1/evidence/xyz", 400),
    (f"/api/v1/markets/pendle:137:{PENDLE}", 400),
    ("/api/v1/markets?chain_id=137", 400),
    (f"/api/v1/series?entity_id=asset:1:{PT}&metric=MARKET_PRICE&start=2026-10-01T00:00:00", 400),
    ("/api/v1/markets?chain_id=abc", 422),
    ("/api/v1/strategies?status=NOPE", 422),
    (f"/api/v1/series?entity_id=asset:1:{PT}&metric=NOT_A_METRIC", 422),
    ("/api/v1/markets?data_mode=FAKE", 422),
])
def test_errors(client, url, code):
    r = client.get(url)
    assert r.status_code == code, r.json()
    assert set(r.json()) == {"error", "detail"}


def test_empty_store_is_503(tmp_path):
    c = TestClient(create_app(db_path=tmp_path / "empty.sqlite", timeseries_root=tmp_path / "ts",
                              clock=lambda: NOW))
    for url in ("/api/v1/markets", "/api/v1/strategies"):
        r = c.get(url)
        assert r.status_code == 503 and r.json()["error"] == "unavailable"
    assert c.get("/api/v1/health").json()["status"] == "MISSING"


def test_stale_market_never_ok(client):
    body = client.get(f"/api/v1/markets/morpho:1:{STALE_MARKET_ID}").json()
    assert body["status"] == "STALE"
    assert any("stale: age_seconds=3600" in w for w in body["warnings"])
    fresh = client.get(f"/api/v1/markets/{MARKET_KEY}").json()
    # fresh, but API-only (no RPC read): its own status is kept, never upgraded to OK
    assert fresh["status"] == "UNVERIFIED" and not any("stale" in w for w in fresh["warnings"])
    listing = client.get("/api/v1/markets").json()
    assert listing["status"] == "STALE"  # worst item wins
    status = client.get(f"/api/v1/data-status?entity_id=morpho:1:{STALE_MARKET_ID}").json()["data"]
    assert status["freshness"] == {"as_of": "2026-10-10T11:00:00+00:00", "age_seconds": 3600,
                                   "ttl_seconds": 300, "is_stale": True}


def test_demo_only_on_request(client):
    real = client.get(f"/api/v1/strategies/{STRAT_ID}").json()
    assert real["snapshot"]["data_mode"] == "REAL" and DEMO_WARNING not in real["warnings"]
    assert real["data"]["strategy"]["status"] == "VERIFIED_ACTIVE"

    demo = client.get(f"/api/v1/strategies/{STRAT_ID}?data_mode=DEMO").json()
    assert demo["snapshot"]["data_mode"] == "DEMO" and DEMO_WARNING in demo["warnings"]
    assert demo["data"]["strategy"]["status"] == "CANDIDATE"

    assert client.get("/api/v1/positions/pos_demo_1").status_code == 404  # DEMO not served as REAL
    assert [s["data_mode"] for s in client.get("/api/v1/strategies").json()["data"]] == ["REAL"]
    assert client.get("/api/v1/markets?data_mode=DEMO").status_code == 503  # no DEMO markets stored
