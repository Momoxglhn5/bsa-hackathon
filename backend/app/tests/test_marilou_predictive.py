"""Synthetic prospective journals; these tests do not validate a real model."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.analytics.dataset import AnalyticsSnapshot, load_snapshot
from app.analytics.service import AnalyticsService
from app.decision.forecast import forecast_carry
from app.main import DEFAULT_SNAPSHOT
from app.models.timeseries import TimeseriesDTO
from app.tests.test_core_analytics import decision_input, request, real_shaped_market
from ml.predictive import (Protocol, IssuedForecast, PricePoint, configuration_hash,
                           digest, evaluate, issue, project, write_new)
from ml.series import prepare_pt_series
from ml.pretrained_candidates import MODEL_IDS


@pytest.fixture
def study():
    demo = load_snapshot(DEFAULT_SNAPSHOT)
    data = real_shaped_market(demo)
    def real(value):
        if isinstance(value, dict):
            if 'data_mode' in value:
                value['data_mode'] = 'REAL'
            if value.get('status') == 'UNVERIFIED':
                value['status'] = 'OK'
            for v in value.values():
                real(v)
        elif isinstance(value, list):
            for v in value:
                real(v)
    real(data)
    for market in data["markets"]:
        market["status"] = "PARTIAL" if market["morpho"] else "UNVERIFIED"
    s = data['strategies'][0]
    s.update(status='VERIFIED_ACTIVE', data_status='OK', block_number=123456,
             verification=[dict(check='synthetic', onchain_value=s['pt']['address'], status='VERIFIED',
                                evidence_ids=s['evidence_ids'])])
    snapshot = AnalyticsSnapshot.model_validate(data)
    now = snapshot.as_of
    strategy = snapshot.strategies[0]
    evidence = snapshot.evidence[0].evidence_id
    protocol = Protocol(frozen_at=now-timedelta(days=100), strategy_id=strategy.strategy_id,
                        entity_id='pendle:1:'+strategy.pendle_market_address, revision='a'*40,
                        runtime_package_version='test', configuration_sha256=configuration_hash('timesfm'))
    values = {now-timedelta(days=i): '1' for i in range(101)}
    cutoffs = [now-timedelta(days=84-7*week) for week in range(12)]
    for cutoff in cutoffs:
        values[cutoff] = '0.9'
        for h in (1,3,7):
            values[cutoff+timedelta(days=h)] = '1.1'
    for cutoff in cutoffs:
        values[cutoff] = '0.9'
    records = []
    for week, cutoff in enumerate(cutoffs):
        points = []
        for h in range(1,8):
            timestamp = cutoff+timedelta(days=h)
            target = Decimal(values[timestamp])
            q = (target+Decimal('0.1'),target+Decimal('0.2'),target+Decimal('0.3')) if week < 2 else (target-Decimal('0.1'),target,target+Decimal('0.1'))
            points.append(PricePoint(timestamp=timestamp,q10=q[0],q50=q[1],q90=q[2]))
        records.append(IssuedForecast(protocol_sha256=digest(protocol), cutoff_at=cutoff,
            issued_at=cutoff+timedelta(minutes=1), source_snapshot_ref='SYNTHETIC',
            source_evidence_ids=(evidence,), last_price=values[cutoff], rolling_mean='1',points=tuple(points)))
    dto = TimeseriesDTO.model_validate(dict(data_mode='REAL', chain_id=1,
        series_id='ts_synthetic_prospective', metric='MARKET_PRICE', subject_kind='ASSET',
        subject_id=strategy.pt.address, unit='USD', pricing_method='API_SPOT', interval_seconds=86400,
        source_id='synthetic', status='OK', points=[dict(timestamp=t, value=v, status='OK',
            evidence_ids=[evidence]) for t,v in sorted(values.items())]))
    latest = IssuedForecast(protocol_sha256=digest(protocol), issued_at=now, cutoff_at=now,
        source_snapshot_ref='SYNTHETIC', source_evidence_ids=(evidence,), last_price=values[now],
        rolling_mean='1', points=tuple(PricePoint(timestamp=now+timedelta(days=h), q10='1',q50='1.1',q90='1.2')
                                    for h in range(1,8)))
    return protocol, records+[latest], dto, snapshot, now


def test_scores_are_asset_and_horizon_specific(study):
    protocol, forecasts, series, snapshot, now = study
    report = evaluate(protocol, forecasts, series, snapshot.strategies[0], now)
    assert [r['n_evaluations'] for r in report['horizons']] == [12,12,12]
    assert [r['status'] for r in report['horizons']] == ['VALIDATED','VALIDATED','NOT_VALIDATED']
    assert 'NO_GAIN_VS_PERSISTENCE' in report['horizons'][2]['reasons']


def test_short_study_is_not_promoted(study):
    p, f, s, snap, now = study
    report = evaluate(p, f[:2], s, snap.strategies[0], now)
    assert all(r['status']=='NOT_VALIDATED' for r in report['horizons'])


@pytest.mark.parametrize('mutation', ['protocol','overlap','revision','target'])
def test_invalid_journals_rejected(study, mutation):
    p, f, s, snap, now = study
    if mutation == 'protocol':
        f[0] = f[0].model_copy(update={'protocol_sha256':'bad'})
    if mutation == 'overlap':
        f.append(f[0])
    if mutation == 'revision':
        f[0] = f[0].model_copy(update={'last_price':Decimal('4')})
    if mutation == 'target':
        p = p.model_copy(update={'entity_id':'pendle:1:0x'+'b'*40})
    with pytest.raises(ValueError):
        evaluate(p,f,s,snap.strategies[0],now)


def test_cannot_issue_after_target_or_rewrite_file(study, tmp_path):
    p, f, *_ = study
    raw = f[0].model_dump()
    raw['issued_at'] = f[0].points[0].timestamp
    with pytest.raises(ValidationError):
        IssuedForecast.model_validate(raw)
    path = tmp_path/'protocol.json'
    write_new(path,p)
    with pytest.raises(FileExistsError):
        write_new(path,p)


def test_issue_checks_frozen_model_and_uses_only_context(study):
    p,f,s,snap,now = study
    data = prepare_pt_series(s,snap.strategies[0],source_snapshot_ref='test',source_as_of=now,retrieved_at=now)
    model = SimpleNamespace(model_id=MODEL_IDS['timesfm'], model_version='timesfm=test;model='+p.revision,
                            predict=lambda context: SimpleNamespace(days=f[-1].points))
    record = issue(p,data,model,now)
    assert record.last_price == data.values[-1]
    model.model_version = 'wrong'
    with pytest.raises(ValueError,match='revision'):
        issue(p,data,model,now)


def test_projection_and_real_decision_integration(study):
    p,f,s,snap,now = study
    pos = snap.positions[0]
    report,bundles,proof = project(p,f,s,snap,pos.position_id,Decimal('0.1'),now)
    assert [b.horizon_days for b in bundles]==[1,3]
    b = bundles[0]
    market = snap.market(pos).morpho
    expected = pos.collateral_amount*(Decimal('1.1')-market.collateral_price.value)-pos.borrow_amount*market.loan_price.value*Decimal('0.1')/365
    assert abs(b.estimated_net_carry_usd-expected) < Decimal('1e-20')
    enriched = snap.model_copy(update={'forecasts':bundles,'evidence':snap.evidence+(proof,)})
    service = AnalyticsService(enriched,clock=lambda:now)
    sim = service.simulate(request(enriched,scenario='UNAUTHORIZED_MINTING',horizon_days=1))
    decision = service.decide(decision_input(enriched,sim,mode='predictive'))
    assert decision.effective_mode=='predictive'
    assert decision.forecast_bundle_id==b.bundle_id
    assert decision.net_carry_usd==b.estimated_net_carry_usd
    assert decision.expected_loss_usd==Decimal('0.02')*b.projected_collateral_value_usd*Decimal('0.2')
    assert 'SCENARIO_HAIRCUT_APPLIED_TO_FORECAST_COLLATERAL' in decision.warnings
    # A position change cannot silently reuse the cached dollar projection.
    changed = enriched.model_copy(update={'positions':(pos.model_copy(update={'collateral_amount':pos.collateral_amount+1}),)})
    assert forecast_carry(changed,decision_input(enriched,sim,mode='predictive'),now)[1]=='FORECAST_INPUTS_CHANGED'
    assert forecast_carry(enriched,decision_input(enriched,sim,mode='predictive'),now+timedelta(minutes=6))[1]=='STALE'


def test_failed_validation_exports_no_bundles_and_missing_proof_blocks(study):
    p,f,s,snap,now = study
    assert project(p,f[:2]+[f[-1]],s,snap,snap.positions[0].position_id,Decimal('0'),now)[1]==()
    with pytest.raises(ValueError,match='evidence|prices'):
        project(p,f,s,snap.model_copy(update={'evidence':()}),snap.positions[0].position_id,Decimal('0'),now)


def test_unknown_loss_mapping_preserves_normal_decision(study):
    from app.decision.engine import decide
    p,f,s,snap,now = study
    _,bundles,proof = project(p,f,s,snap,snap.positions[0].position_id,Decimal('0'),now)
    enriched = snap.model_copy(update={'forecasts':bundles,'evidence':snap.evidence+(proof,)})
    service = AnalyticsService(enriched,clock=lambda:now)
    result = service.simulate(request(enriched,scenario='UNAUTHORIZED_MINTING',horizon_days=1))
    # A non-proportional loss must not be scaled as though it were a haircut.
    effect = result.position_effects[result.position_id]
    result = result.model_copy(update={'position_effects':{result.position_id:effect.model_copy(
        update={'scenario_estimated_loss_usd':effect.scenario_estimated_loss_usd+1})}})
    normal = decide(enriched,decision_input(enriched,result),{result.simulation_id:result},now)
    predictive = decide(enriched,decision_input(enriched,result,mode='predictive'),{result.simulation_id:result},now)
    assert predictive.fallback_reason=='FORECAST_LOSS_MAPPING_UNAVAILABLE'
    assert predictive.effective_mode=='normal'
    assert predictive.forecast_bundle_id is None
    assert predictive.net_carry_usd==normal.net_carry_usd
    assert predictive.expected_loss_usd==normal.expected_loss_usd
    assert predictive.stay_value_usd==normal.stay_value_usd
