"""The jury demo dataset must keep every screen populated through the real engines."""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_demo_dataset.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_demo_dataset", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve postponed annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_demo_dataset_feeds_every_engine(tmp_path, monkeypatch):
    builder = load_builder()
    out = tmp_path / "demo"
    dataset = builder.build(datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc))
    builder.write(dataset, out, None)
    monkeypatch.delenv("ANALYTICS_SNAPSHOT", raising=False)
    monkeypatch.setenv("ANALYTICS_INPUTS", str(out / "analytics_inputs.json"))
    client = TestClient(create_app(out / builder.DB_FILENAME, out / "timeseries", replay=True))
    q = "?data_mode=DEMO"

    catalog = client.get("/api/v1/catalog" + q).json()["data"]
    assert len(catalog["positions"]) == 7 and len(catalog["strategies"]) == 5
    assert all(p["data_mode"] == "DEMO" for p in catalog["positions"])
    for strategy in catalog["strategies"]:
        risk = client.get(f"/api/v1/risk/{strategy['strategy_id']}" + q).json()["data"]
        assert risk["risk_status"] == "AVAILABLE" and risk["unknown_factors"] == []
        # DEMO evidence is never presented as high-confidence on-chain proof.
        assert float(risk["evidence_confidence_pct"]) <= 50
        graph = client.get(f"/api/v1/graph/{strategy['strategy_id']}" + q).json()["data"]
        assert {e["verification_status"] for e in graph["edges"]} <= {"HYPOTHESIS", "UNRESOLVED"}

    position = next(p for p in catalog["positions"] if p["position_id"] == "pos_atlas_susde")
    sid, pid = position["strategy_id"], position["position_id"]
    sim = client.post("/api/v1/simulations" + q, json={
        "strategy_id": sid, "position_id": pid, "scenario_type": "UNAUTHORIZED_MINTING", "scenario_scope": "HYPOTHETICAL",
        "target_node_id": "asset:1:" + position["collateral_asset"]["address"], "horizon_days": 7,
        "parameters": {"assumed_token_haircut_ratio": "0.2"}}).json()["data"]
    quote = next(x["quote_id"] for x in catalog["quotes"] if x["position_id"] == pid)
    verdicts = []
    for probability in ("0.02", "0.10"):
        decision = client.post("/api/v1/decisions" + q, json={
            "strategy_id": sid, "position_id": pid, "mode": "predictive", "horizon_days": 7, "exit_quote_id": quote,
            "scenarios_mutually_exclusive": True,
            "scenario_assumptions": [{"loss_scenario_ref": sim["simulation_id"], "probability_over_horizon": probability}]}).json()["data"]
        assert decision["decision_status"] == "CONDITIONAL" and decision["effective_mode"] == "predictive"
        verdicts.append(decision["verdict"])
    assert verdicts == ["STAY", "EXIT"]

    mc = client.post("/api/v1/monte-carlo" + q, json={
        "strategy_id": sid, "position_id": pid, "horizon_days": 7, "num_paths": 1000, "seed": 1,
        "volatility_source": "USER_ASSUMPTION", "annual_volatility": "0.25", "annual_drift": "0"}).json()
    assert mc["data"]["status"] == "OK"
    series = client.get("/api/v1/series" + q + "&metric=ORACLE_PRICE&entity_id=morpho:1:" + position["market_id"])
    assert series.status_code == 200 and len(series.json()["data"]["points"]) == builder.HISTORY_DAYS + 1
