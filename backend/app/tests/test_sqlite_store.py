"""SqliteStore round trips and write rules (tmp_path, no network)."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.ingestion.normalize import SourceRef, normalize_morpho_market
from app.models.evidence import (
    DataMode,
    DataStatus,
    EvidenceDTO,
    RelationStatus,
    RelationType,
    SourceKind,
    make_relation,
)
from app.models.market import Protocol, TokenRef
from app.models.position import PositionDTO, PositionKind
from app.models.strategy import StrategyDTO, StrategyStatus, make_strategy_id
from app.storage.sqlite_store import SqliteStore

T0 = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
MARKET_ID = "0x" + "ab" * 32
PT = "0x" + "11" * 20
LOAN = "0x" + "22" * 20
EV = "ev_" + "a" * 24


@pytest.fixture
def store(tmp_path):
    with SqliteStore(tmp_path / "sub" / "test.sqlite") as s:
        yield s


def evidence(mode=DataMode.REAL, method="eth_call decimals()") -> EvidenceDTO:
    real = mode == DataMode.REAL
    return EvidenceDTO(
        chain_id=1, data_mode=mode, evidence_id=EV,
        source_kind=SourceKind.RPC if real else SourceKind.FIXTURE,
        source_id="ethereum_rpc" if real else "fixture",
        method=method, block_number=100 if real else None, fetched_at=T0, status=DataStatus.OK,
        decoded_excerpt={"out0": "6"}, parser_version="ethereum_rpc.v1",
    )


def market(as_of=T0, mode=DataMode.REAL):
    item = {
        "market_id": MARKET_ID, "chain_id": 1, "loan_token": LOAN, "collateral_token": PT,
        "oracle_address": "0x" + "33" * 20, "irm_address": "0x" + "44" * 20,
        "lltv_wad": "860000000000000000",
    }
    return normalize_morpho_market(item, SourceRef("morpho_api", EV, as_of), data_mode=mode).dto


def strategy(as_of=T0, mode=DataMode.REAL, status=StrategyStatus.CANDIDATE, reason="address match"):
    return StrategyDTO(
        chain_id=1, data_mode=mode, strategy_id=make_strategy_id(1, MARKET_ID, PT),
        status=status, status_reason=reason, data_status=DataStatus.UNVERIFIED,
        morpho_market_id=MARKET_ID, pt=TokenRef(chain_id=1, address=PT),
        loan_asset=TokenRef(chain_id=1, address=LOAN, decimals=6),
        expiry=T0 + timedelta(days=90), as_of=as_of, source_ids=("morpho_api",), evidence_ids=(EV,),
    )


def relation(observed_at=T0):
    return make_relation(
        source_node_id="asset:1:0x" + "55" * 20, target_node_id="asset:1:0x" + "66" * 20,
        relation_type=RelationType.ECONOMIC_BACKING, verification_status=RelationStatus.UNRESOLVED,
        evidence=[evidence()], observed_at=observed_at, note="assetInfo/yieldToken do not prove backing",
    )


def position():
    return PositionDTO(
        chain_id=1, data_mode=DataMode.DEMO, position_id="pos_demo_1", kind=PositionKind.SIMULATED,
        market_id=MARKET_ID, collateral_asset=TokenRef(chain_id=1, address=PT, decimals=18),
        loan_asset=TokenRef(chain_id=1, address=LOAN, decimals=6),
        collateral_amount=Decimal("1234.567890123456789012"),
        collateral_amount_raw=1234567890123456789012,
        borrow_amount=Decimal("1000.000001"), borrow_amount_raw=1000000001,
        as_of=T0, status=DataStatus.OK, source_ids=("user_input",),
    )


def test_round_trip_all_types(store):
    ev, m, s, r, p = evidence(), market(), strategy(), relation(), position()
    store.save_evidence([ev])
    store.save_markets([m])
    store.save_strategies([s])
    store.save_relations([r])
    store.save_positions([p])
    assert store.get_evidence(EV) == ev
    assert store.get_market(m.market_key) == m
    assert store.get_strategy(s.strategy_id) == s
    assert store.list_relations("asset:1:0x" + "66" * 20) == [r]
    loaded = store.get_position("pos_demo_1", DataMode.DEMO)
    assert loaded == p
    assert loaded.collateral_amount == Decimal("1234.567890123456789012")
    assert store.list_markets(Protocol.MORPHO_BLUE) == [m]
    assert store.list_markets(Protocol.PENDLE_V2) == []


def test_evidence_is_immutable(store):
    original = evidence()
    assert store.save_evidence([original]) == 1
    assert store.save_evidence([evidence(method="eth_call other()")]) == 0
    assert store.get_evidence(EV) == original
    assert store.counts()["evidence"] == 1


def test_older_does_not_overwrite_newer(store):
    newer = strategy(as_of=T0, reason="newer")
    store.save_strategies([newer])
    assert store.save_strategies([strategy(as_of=T0 - timedelta(seconds=1), reason="older")]) == 0
    assert store.get_strategy(newer.strategy_id).status_reason == "newer"

    store.save_markets([market(as_of=T0)])
    assert store.save_markets([market(as_of=T0 - timedelta(days=1))]) == 0
    assert store.get_market(market().market_key).as_of == T0

    store.save_relations([relation(T0)])
    assert store.save_relations([relation(T0 - timedelta(hours=1))]) == 0

    # equal or newer replaces
    assert store.save_strategies([strategy(as_of=T0 + timedelta(seconds=1), reason="newest")]) == 1
    assert store.get_strategy(newer.strategy_id).status_reason == "newest"


def test_real_and_demo_do_not_mix(store):
    store.save_strategies([strategy(mode=DataMode.REAL), strategy(mode=DataMode.DEMO, reason="demo")])
    store.save_markets([market(mode=DataMode.REAL), market(mode=DataMode.DEMO)])
    store.save_evidence([evidence(DataMode.REAL), evidence(DataMode.DEMO)])
    real = store.list_strategies()
    demo = store.list_strategies(data_mode=DataMode.DEMO)
    assert [s.data_mode for s in real] == [DataMode.REAL]
    assert [s.status_reason for s in demo] == ["demo"]
    assert [m.data_mode for m in store.list_markets()] == [DataMode.REAL]
    assert [m.data_mode for m in store.list_markets(data_mode=DataMode.DEMO)] == [DataMode.DEMO]
    assert store.get_evidence(EV, DataMode.DEMO).source_kind == SourceKind.FIXTURE
    assert store.list_strategies(StrategyStatus.VERIFIED_ACTIVE) == []


def test_unknown_id_is_none(store):
    assert store.get_evidence("ev_" + "f" * 24) is None
    assert store.get_market("morpho:1:0x" + "00" * 32) is None
    assert store.get_strategy("strat_" + "0" * 24) is None
    assert store.get_position("pos_none") is None
    assert store.latest_raw_snapshot() is None


def test_raw_snapshot_meta(store, tmp_path):
    for sid, started in (("snap_a", "2026-10-09T10:00:00Z"), ("snap_b", "2026-10-10T10:00:00Z")):
        d = tmp_path / sid
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({
            "snapshot_id": sid, "data_mode": "REAL", "chain_id": 1, "started_at": started,
            "finished_at": started, "complete": True, "records": [{}, {}], "errors": [],
        }))
        store.save_raw_snapshot_meta(d)
    store.save_raw_snapshot_meta(tmp_path / "snap_a")  # ignored
    latest = store.latest_raw_snapshot()
    assert latest.snapshot_id == "snap_b" and latest.record_count == 2 and latest.complete
    assert store.counts()["raw_snapshots"] == 2


def test_corrupt_row_raises(store):
    store.save_strategies([strategy()])
    store._conn.execute("UPDATE strategies SET payload = '{\"status\": \"BOGUS\"}'")
    with pytest.raises(ValueError):
        store.get_strategy(strategy().strategy_id)


def test_schema_idempotent(tmp_path):
    path = tmp_path / "db.sqlite"
    SqliteStore(path).close()
    with SqliteStore(path) as s:
        assert s._conn.execute("PRAGMA user_version").fetchone()[0] == 1
    with SqliteStore(":memory:") as s:
        assert s.counts()["markets"] == 0
