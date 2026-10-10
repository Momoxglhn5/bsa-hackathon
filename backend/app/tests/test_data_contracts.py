"""Relation contract: only OK RPC evidence at one block can yield ONCHAIN_VERIFIED."""
from datetime import datetime, timezone

import pytest

from app.models.evidence import (
    DataMode,
    DataStatus,
    EvidenceDTO,
    RelationStatus,
    RelationType,
    SourceKind,
    make_relation,
)

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
SRC = "pendle:1:0x" + "11" * 20
DST = "asset:1:0x" + "22" * 20


def evidence(n: str, kind: SourceKind, block: int | None = None, status=DataStatus.OK) -> EvidenceDTO:
    return EvidenceDTO(
        chain_id=1, data_mode=DataMode.REAL, evidence_id="ev_" + n * 24, source_kind=kind,
        source_id="ethereum_rpc" if kind == SourceKind.RPC else "pendle_api",
        method="eth_call readTokens()", block_number=block, fetched_at=NOW, status=status,
        error_message="boom" if status == DataStatus.ERROR else None,
    )


def relation(evs: list[EvidenceDTO], status=RelationStatus.ONCHAIN_VERIFIED, note=None):
    return make_relation(
        source_node_id=SRC, target_node_id=DST, relation_type=RelationType.PENDLE_MARKET_PT,
        verification_status=status, evidence=evs, observed_at=NOW, note=note,
    )


def test_rpc_evidence_gives_onchain_verified():
    r = relation([evidence("a", SourceKind.RPC, block=100)])
    assert r.verification_status == RelationStatus.ONCHAIN_VERIFIED
    assert r.block_number == 100


def test_api_evidence_never_onchain_verified():
    with pytest.raises(ValueError):
        relation([evidence("b", SourceKind.PENDLE_API)])


def test_error_rpc_evidence_never_onchain_verified():
    with pytest.raises(ValueError):
        relation([evidence("c", SourceKind.RPC, block=100, status=DataStatus.ERROR)])


def test_evidence_across_blocks_rejected():
    with pytest.raises(ValueError):
        relation([evidence("d", SourceKind.RPC, block=100), evidence("e", SourceKind.RPC, block=101)])


def test_api_evidence_can_be_api_observed():
    r = relation([evidence("f", SourceKind.PENDLE_API)], status=RelationStatus.API_OBSERVED)
    assert r.verification_status == RelationStatus.API_OBSERVED
    assert r.block_number is None


def test_unresolved_requires_note():
    with pytest.raises(ValueError):
        relation([], status=RelationStatus.UNRESOLVED)
    assert relation([], status=RelationStatus.UNRESOLVED, note="backing unknown").note



# ---------------------------------------------------------------------------
# DEMO UI fixture (ui_mock_data_v1.json)
# ---------------------------------------------------------------------------

import copy  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.api.routes_data import DEMO_WARNING  # noqa: E402
from app.main import create_app  # noqa: E402
from app.storage.parquet_store import ParquetStore  # noqa: E402
from app.storage.sqlite_store import SqliteStore  # noqa: E402

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND / "scripts"))
from build_ui_fixture import FIXTURE_PATH, render  # noqa: E402
from load_demo_fixture import MODELS, NotDemoError, load_fixture  # noqa: E402

MANIFEST = json.loads((_BACKEND / "app" / "fixtures" / "demo_strategy_manifest.json").read_text())
FIXTURE = json.loads(FIXTURE_PATH.read_text())
_HEX_RE = re.compile(r"0x[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?")


def _objects():
    return [(key, obj) for key in MODELS for obj in FIXTURE[key]]


def test_fixture_is_up_to_date():
    assert FIXTURE_PATH.read_text() == render(), "run: python scripts/build_ui_fixture.py"


def test_fixture_objects_validate_and_roundtrip():
    assert FIXTURE["schema_version"] == "data-contract-v1.0" and FIXTURE["data_mode"] == "DEMO"
    for key, obj in _objects():
        assert MODELS[key].model_validate(obj).model_dump(mode="json") == obj, key


def test_fixture_is_demo_only():
    for key, obj in _objects():
        assert obj["data_mode"] == "DEMO", key
    assert {e["source_kind"] for e in FIXTURE["evidence"]} <= {"FIXTURE", "USER_INPUT", "DERIVED"}
    assert all(r["verification_status"] != "ONCHAIN_VERIFIED" for r in FIXTURE["relations"])


def test_fixture_evidence_ids_resolve():
    known = {e["evidence_id"] for e in FIXTURE["evidence"]}
    text = json.dumps({k: FIXTURE[k] for k in MODELS if k != "evidence"})
    assert set(re.findall(r"ev_[0-9a-f]{24}", text)) <= known


def test_fixture_has_explicit_gaps():
    morpho, pendle = FIXTURE["markets"]
    assert morpho["status"] == pendle["status"] == "PARTIAL"
    assert morpho["morpho"]["oracle_price"] is None and pendle["pendle"]["liquidity_usd"] is None
    assert any(p["value"] is None and p["status"] == "MISSING" for s in FIXTURE["series"] for p in s["points"])
    text = json.dumps(FIXTURE).lower()
    assert "health_factor" not in text and "risk_score" not in text and "recommend" not in text


def test_fixture_never_reuses_real_addresses():
    real = {v.lower() for v in MANIFEST.values() if isinstance(v, str) and _HEX_RE.fullmatch(v)}
    real.add("0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb")  # Morpho Blue singleton
    found = {h.lower() for h in _HEX_RE.findall(json.dumps(FIXTURE))}
    assert real and not (found & real)
    symbols = {t["symbol"] for m in FIXTURE["markets"] for t in _tokens(m) if t and t.get("symbol")}
    assert symbols and all(s.startswith("DEMO-") for s in symbols)


def _tokens(market):
    f = market["morpho"] or market["pendle"]
    return [v for v in f.values() if isinstance(v, dict) and "address" in v]


def test_loader_refuses_real_objects(tmp_path):
    bad = copy.deepcopy(FIXTURE)
    bad["strategies"][0]["data_mode"] = "REAL"
    with SqliteStore(tmp_path / "db.sqlite") as s:
        with pytest.raises(NotDemoError):
            load_fixture(bad, s, ParquetStore(tmp_path / "ts"))
        assert s.counts() == {k: 0 for k in s.counts()}  # nothing written


def test_loaded_fixture_served_only_as_demo(tmp_path):
    db, ts = tmp_path / "db.sqlite", tmp_path / "ts"
    with SqliteStore(db) as s:
        first = load_fixture(FIXTURE, s, ParquetStore(ts))
        again = load_fixture(FIXTURE, s, ParquetStore(ts))
        assert first["evidence"] == 8 and again["evidence"] == 0
        assert s.counts()["markets"] == 2 and s.counts()["relations"] == 10
    assert len(ParquetStore(ts).read_observations(
        "pendle:1:0x" + "e" * 40, "PT_IMPLIED_APY", DataMode.DEMO)) == 7  # idempotent

    client = TestClient(create_app(db_path=db, timeseries_root=ts))
    strat = FIXTURE["strategies"][0]["strategy_id"]
    market = "morpho:1:0x" + "d" * 64
    for url in (f"/api/v1/strategies/{strat}", f"/api/v1/markets/{market}", "/api/v1/positions/pos_demo_ui_1",
                f"/api/v1/series?entity_id=pendle:1:0x{'e' * 40}&metric=PT_IMPLIED_APY"):
        demo = client.get(url + ("&" if "?" in url else "?") + "data_mode=DEMO")
        assert demo.status_code == 200, (url, demo.json())
        assert DEMO_WARNING in demo.json()["warnings"] and demo.json()["snapshot"]["data_mode"] == "DEMO"
        assert client.get(url).status_code == 404, url  # default REAL: not returned
    assert client.get("/api/v1/markets").status_code == 503  # no REAL data at all
