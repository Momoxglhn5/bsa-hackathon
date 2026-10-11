"""Archived RPC inputs and synthetic fixtures test different validation stages."""
import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest
from app.analytics.dataset import AnalyticsSnapshot, load_snapshot
from app.models.evidence import DataMode
from app.tests.test_monte_carlo_pr2 import world,NOW

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('validate_mc',ROOT/'backend/scripts/validate_monte_carlo_real.py')
runner=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def real_snapshot():
    return load_snapshot(ROOT/'docs/validation/monte_carlo_20261011/snapshot.json')


def test_archived_real_market_run_preserves_missing_calibration(monkeypatch):
    import httpx
    def no_network(*args,**kwargs):
        raise AssertionError('replay must not fetch providers')
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',no_network)
    snapshot=real_snapshot()
    p=snapshot.positions[0]
    report=runner.validate(snapshot,p.strategy_id,p.position_id,assumed_volatility=Decimal('0.25'),num_paths=1000)
    assert report['status']=='PARTIAL_VALIDATION'
    assert report['available_daily_samples']==11
    assert report['missing_daily_samples']==20
    assert all(report['checks'].values())
    observed=[r for r in report['runs'] if r['source']=='OBSERVED_ORACLE_SERIES']
    assert len(observed)==3
    assert all(r['result']['status']=='UNAVAILABLE' and r['result']['data'] is None for r in observed)
    assumed=[r for r in report['runs'] if r['source']=='USER_ASSUMPTION']
    assert all(r['result']['status']=='OK' and r['result']['provenance']['oracle_calibration'] is None for r in assumed)


def test_complete_synthetic_window_can_exercise_observed_pipeline():
    # This passing fixture tests the branch, not actual on-chain performance.
    w=world()
    snapshot=AnalyticsSnapshot(snapshot_id='SYNTHETIC_ONLY',as_of=NOW,description='Synthetic evidence',
        markets=(w.market,),strategies=(w.strategy,),positions=(w.onchain,),
        series=tuple(w.series),evidence=tuple(w.evidence))
    report=runner.validate(snapshot,w.strategy.strategy_id,w.onchain.position_id,num_paths=1000)
    assert report['status']=='OBSERVED_PIPELINE_CHECKS_PASSED'
    assert report['available_daily_samples']==31
    assert report['missing_daily_samples']==0
    assert all(report['checks'].values())


def test_real_input_zero_volatility_matches_deterministic_control():
    snapshot=real_snapshot()
    p=snapshot.positions[0]
    report=runner.validate(snapshot,p.strategy_id,p.position_id,assumed_volatility=Decimal('0'),num_paths=1000)
    assert report['status']=='PARTIAL_VALIDATION'
    for run in report['runs']:
        if run['source']=='USER_ASSUMPTION':
            data=run['result']['data']
            assert data['breached_paths']==0
            assert Decimal(data['oracle_mark_to_model_var95_loan_units'])==0
            assert Decimal(data['oracle_mark_to_model_es95_loan_units'])==0
            assert data['realized_liquidation_loss_usd'] is None


def test_demo_cannot_be_mislabeled_real_validation():
    snap=real_snapshot()
    market=snap.market(snap.positions[0])
    changed=snap.model_copy(update={'markets':tuple(m.model_copy(update={'data_mode':DataMode.DEMO})
        if m.market_key==market.market_key else m for m in snap.markets)})
    with pytest.raises(ValueError,match='REAL Data'):
        runner.validate(changed,snap.positions[0].strategy_id,snap.positions[0].position_id)
