"""Synthetic prospective journals; these tests do not validate a real model."""
import hashlib
import json
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
from app.models.evidence import EvidenceDTO
from app.tests.test_core_analytics import decision_input, request, real_shaped_market
from ml.predictive import (Protocol, IssuedForecast, PricePoint, configuration_hash,
                           digest, evaluate, issue, project, write_new)
from ml.series import prepare_pt_series
from ml.pretrained_candidates import MODEL_IDS


@pytest.fixture
def study():
    # Fake Pendle responses and real-shaped DTOs, never actual market evidence.
    demo = load_snapshot(DEFAULT_SNAPSHOT)
    data = json.loads(json.dumps(real_shaped_market(demo)).replace('T12:00:00Z','T00:00:00Z'))
    def real(value):
        if isinstance(value, dict):
            if 'data_mode' in value:
                value['data_mode'] = 'REAL'
            for v in value.values():
                real(v)
        elif isinstance(value, list):
            for v in value:
                real(v)
    real(data)
    s = data['strategies'][0]
    s.update(status='VERIFIED_ACTIVE',data_status='OK',block_number=123456,
             verification=[dict(check='synthetic',onchain_value=s['pt']['address'],status='VERIFIED',
                                evidence_ids=s['evidence_ids'])])
    snap = AnalyticsSnapshot.model_validate(data)
    now = snap.as_of
    strategy = snap.strategies[0]
    protocol = Protocol(frozen_at=now-timedelta(days=100),strategy_id=strategy.strategy_id,
        entity_id='pendle:1:'+strategy.pendle_market_address,revision='a'*40,
        runtime_package_version='test',configuration_sha256=configuration_hash('timesfm'))
    values = {now-timedelta(days=i): '1' for i in range(145)}
    cutoffs = [now-timedelta(days=84-7*week) for week in range(12)]
    for cutoff in cutoffs:
        for h in (1,3,7):
            values[cutoff+timedelta(days=h)] = '1.1'
    for cutoff in cutoffs:
        values[cutoff] = '0.9'
    raw = {}
    proofs = list(snap.evidence)
    def history(at,index):
        identity = 'ev_'+format(index,'024x')
        entries = [(t,v) for t,v in sorted(values.items()) if t<=at]
        body = json.dumps(dict(currency='USD',timeFrame='day',total=len(entries),
            results='time,close\n'+''.join(str(int((t-timedelta(days=1)).timestamp()))+','+v+'\n' for t,v in entries))).encode()
        raw[identity] = body
        proofs.append(EvidenceDTO(data_mode='REAL',chain_id=1,evidence_id=identity,
            source_kind='PENDLE_API',source_id='pendle_api',method='rest:/v4/prices/ohlcv',
            target_address=strategy.pt.address,fetched_at=at,payload_sha256=hashlib.sha256(body).hexdigest(),status='OK',
            request_params={'time_frame':'day','timestamp_start':(entries[0][0]-timedelta(days=1)).isoformat(),
                            'timestamp_end':at.isoformat()}))
        return TimeseriesDTO.model_validate(dict(data_mode='REAL',chain_id=1,series_id='ts_synthetic_prospective',
            metric='MARKET_PRICE',subject_kind='ASSET',subject_id=strategy.pt.address,unit='USD',
            pricing_method='API_SPOT',interval_seconds=86400,source_id='pendle_api',status='OK',
            points=[dict(timestamp=t,value=v,status='OK',evidence_ids=[identity]) for t,v in entries]))
    records = []
    for week,cutoff in enumerate(cutoffs):
        ctx = history(cutoff,week+1)
        points = []
        for h in range(1,8):
            timestamp = cutoff+timedelta(days=h)
            target = Decimal(values[timestamp])
            q = (target+Decimal('0.1'),target+Decimal('0.2'),target+Decimal('0.3')) if week<2 else (target-Decimal('0.1'),target,target+Decimal('0.1'))
            points.append(PricePoint(timestamp=timestamp,q10=q[0],q50=q[1],q90=q[2]))
        records.append(IssuedForecast(protocol_sha256=digest(protocol),cutoff_at=cutoff,issued_at=cutoff+timedelta(minutes=1),
            source_snapshot_ref='sha256:'+digest(ctx),source_evidence_ids=ctx.points[-1].evidence_ids,
            last_price=values[cutoff],rolling_mean=sum(p.value for p in ctx.points[-7:])/7,
            points=tuple(points),context_series=ctx))
    dto = history(now,13)
    records.append(IssuedForecast(protocol_sha256=digest(protocol),issued_at=now,cutoff_at=now,
        source_snapshot_ref='sha256:'+digest(dto),source_evidence_ids=dto.points[-1].evidence_ids,
        last_price=values[now],rolling_mean=sum(p.value for p in dto.points[-7:])/7,context_series=dto,
        points=tuple(PricePoint(timestamp=now+timedelta(days=h),q10='1',q50='1.1',q90='1.2') for h in range(1,8))))
    market = data['markets'][0]['morpho']
    identity = 'ev_'+format(99,'024x')
    prices = {'1-'+p['base_asset']['address']:p['value'] for p in (market['collateral_price'],market['loan_price'])}
    body = json.dumps({'prices':prices}).encode()
    raw[identity] = body
    proofs.append(EvidenceDTO(data_mode='REAL',chain_id=1,evidence_id=identity,source_kind='PENDLE_API',source_id='pendle_api',
        method='rest:/v1/prices/assets',fetched_at=now,status='OK',payload_sha256=hashlib.sha256(body).hexdigest(),
        request_params={'ids':','.join(prices),'chainId':'1'}))
    for price in (market['collateral_price'],market['loan_price']):
        price['evidence_ids'] = [identity]
    data['evidence'] = [p.model_dump(mode='json') for p in proofs]
    snapshot = AnalyticsSnapshot.model_validate(data)
    return protocol,records,dto,snapshot,now,raw


def test_scores_are_asset_and_horizon_specific(study):
    protocol, forecasts, series, snapshot, now, raw = study
    report = evaluate(protocol, forecasts, series, snapshot.strategies[0], now,evidence={e.evidence_id:e for e in snapshot.evidence},raw_evidence=raw)
    assert [r['n_evaluations'] for r in report['horizons']] == [12,12,12]
    assert [r['status'] for r in report['horizons']] == ['VALIDATED','VALIDATED','NOT_VALIDATED']
    assert 'NO_GAIN_VS_PERSISTENCE' in report['horizons'][2]['reasons']


def test_short_study_is_not_promoted(study):
    p, f, s, snap, now, raw = study
    report = evaluate(p, f[:2], s, snap.strategies[0], now,evidence={e.evidence_id:e for e in snap.evidence},raw_evidence=raw)
    assert all(r['status']=='NOT_VALIDATED' for r in report['horizons'])


@pytest.mark.parametrize('mutation', ['protocol','overlap','revision','target'])
def test_invalid_journals_rejected(study, mutation):
    p, f, s, snap, now, raw = study
    if mutation == 'protocol':
        f[0] = f[0].model_copy(update={'protocol_sha256':'bad'})
    if mutation == 'overlap':
        f.append(f[0])
    if mutation == 'revision':
        f[0] = f[0].model_copy(update={'last_price':Decimal('4')})
    if mutation == 'target':
        p = p.model_copy(update={'entity_id':'pendle:1:0x'+'b'*40})
    with pytest.raises(ValueError):
        evaluate(p,f,s,snap.strategies[0],now,evidence={e.evidence_id:e for e in snap.evidence},raw_evidence=raw)


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
    p,f,s,snap,now,raw = study
    data = prepare_pt_series(s,snap.strategies[0],source_snapshot_ref='sha256:'+digest(s),source_as_of=now,retrieved_at=now)
    model = SimpleNamespace(model_id=MODEL_IDS['timesfm'], model_version='timesfm=test;model='+p.revision,
                            predict=lambda context: SimpleNamespace(days=f[-1].points))
    record = issue(p,data,model,observations=s,evidence={e.evidence_id:e for e in snap.evidence},raw_evidence=raw,clock=lambda:now)
    assert record.last_price == data.values[-1]
    model.model_version = 'wrong'
    with pytest.raises(ValueError,match='revision'):
        issue(p,data,model,observations=s,evidence={e.evidence_id:e for e in snap.evidence},raw_evidence=raw,clock=lambda:now)


def test_projection_and_real_decision_integration(study):
    p,f,s,snap,now,raw = study
    pos = snap.positions[0]
    report,bundles,proof = project(p,f,s,snap,pos.position_id,Decimal('0.1'),now,raw_evidence=raw)
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
    p,f,s,snap,now,raw = study
    assert project(p,f[:2]+[f[-1]],s,snap,snap.positions[0].position_id,Decimal('0'),now,raw_evidence=raw)[1]==()
    with pytest.raises(ValueError,match='evidence|prices'):
        project(p,f,s,snap.model_copy(update={'evidence':()}),snap.positions[0].position_id,Decimal('0'),now,raw_evidence=raw)


def test_unknown_loss_mapping_preserves_normal_decision(study):
    from app.decision.engine import decide
    p,f,s,snap,now,raw = study
    _,bundles,proof = project(p,f,s,snap,snap.positions[0].position_id,Decimal('0'),now,raw_evidence=raw)
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


@pytest.mark.parametrize('mutation', ['user_input','wrong_asset','wrong_source','wrong_method',
                                     'wrong_query_date','wrong_frequency','late_retrieval','wrong_value',
                                     'wrong_point_date','missing_raw','changed_raw','wrong_hash','baseline'])
def test_unrelated_or_tampered_price_proof_cannot_publish(study, mutation):
    p,f,s,snap,now,raw = study
    identity = s.points[0].evidence_ids[0]
    evidence = next(e for e in snap.evidence if e.evidence_id==identity)
    updates = {}
    if mutation=='user_input':
        updates={'source_kind':'USER_INPUT','target_address':'0x'+'f'*40}
    if mutation=='wrong_asset':
        updates={'target_address':'0x'+'f'*40}
    if mutation=='wrong_source':
        updates={'source_id':'other_api'}
    if mutation=='wrong_method':
        updates={'method':'rest:/v1/prices/assets'}
    if mutation=='wrong_query_date':
        updates={'request_params':{**evidence.request_params,'timestamp_start':now.isoformat()}}
    if mutation=='wrong_frequency':
        updates={'request_params':{**evidence.request_params,'time_frame':'hour'}}
    if mutation=='late_retrieval':
        # A forecast cannot use a price response first retrieved after it was issued.
        identity=f[0].source_evidence_ids[0]
        evidence=next(e for e in snap.evidence if e.evidence_id==identity)
        updates={'fetched_at':now}
    if mutation=='wrong_value':
        point=s.points[10].model_copy(update={'value':Decimal('7')})
        s=s.model_copy(update={'points':s.points[:10]+(point,)+s.points[11:]})
    if mutation=='wrong_point_date':
        point=s.points[10].model_copy(update={'timestamp':s.points[10].timestamp+timedelta(hours=1)})
        s=s.model_copy(update={'points':s.points[:10]+(point,)+s.points[11:]})
    if mutation=='missing_raw':
        raw={i:b for i,b in raw.items() if i!=identity}
    if mutation=='changed_raw':
        raw={**raw,identity:raw[identity]+b' '}
    if mutation=='wrong_hash':
        updates={'payload_sha256':'0'*64}
    if mutation=='baseline':
        f[0]=f[0].model_copy(update={'rolling_mean':Decimal('0.0001')})
    if updates:
        # All mutated evidence still passes the shared DTO schema.
        replacement=EvidenceDTO.model_validate({**evidence.model_dump(),**updates})
        snap=snap.model_copy(update={'evidence':tuple(replacement if e.evidence_id==identity else e for e in snap.evidence)})
    with pytest.raises(ValueError):
        project(p,f,s,snap,snap.positions[0].position_id,Decimal('0'),now,raw_evidence=raw)


def test_emission_timestamp_is_sampled_after_inference(study):
    p,f,s,snap,now,raw=study
    data=prepare_pt_series(s,snap.strategies[0],source_snapshot_ref='sha256:'+digest(s),source_as_of=now,retrieved_at=now)
    current=[now]
    def predict(context):
        current[0]=now+timedelta(seconds=2)
        return SimpleNamespace(days=f[-1].points)
    model=SimpleNamespace(model_id=MODEL_IDS['timesfm'],model_version='timesfm=test;model='+p.revision,predict=predict)
    emitted=issue(p,data,model,observations=s,evidence={e.evidence_id:e for e in snap.evidence},raw_evidence=raw,clock=lambda:current[0])
    assert emitted.issued_at==now+timedelta(seconds=2)
    def late(context):
        current[0]=now+timedelta(days=1,seconds=1)
        return SimpleNamespace(days=f[-1].points)
    current[0]=now
    model.predict=late
    with pytest.raises(ValueError,match='stale context|before every target'):
        issue(p,data,model,observations=s,evidence={e.evidence_id:e for e in snap.evidence},raw_evidence=raw,clock=lambda:current[0])


def test_spot_with_unrelated_proof_cannot_publish(study):
    p,f,s,snap,now,raw=study
    identity=snap.market(snap.positions[0]).morpho.collateral_price.evidence_ids[0]
    e=next(e for e in snap.evidence if e.evidence_id==identity)
    unrelated=EvidenceDTO.model_validate({**e.model_dump(),'source_kind':'USER_INPUT','target_address':'0x'+'f'*40})
    snap=snap.model_copy(update={'evidence':tuple(unrelated if proof.evidence_id==identity else proof for proof in snap.evidence)})
    with pytest.raises(ValueError,match='price evidence'):
        project(p,f,s,snap,snap.positions[0].position_id,Decimal('0'),now,raw_evidence=raw)


def test_raw_archive_loader_accepts_collector_and_delivery_and_rejects_conflicts(study,tmp_path):
    from ml.predictive import load_raw_evidence
    _,_,series,snap,_,raw=study
    identity=series.points[0].evidence_ids[0]
    collector=tmp_path/'collector'
    collector.mkdir()
    collector.joinpath('raw_ohlcv.json').write_bytes(raw[identity])
    proof=next(e for e in snap.evidence if e.evidence_id==identity)
    collector.joinpath('evidence.json').write_text(proof.model_dump_json())
    delivery=tmp_path/'delivery'/'raw'
    delivery.mkdir(parents=True)
    for i,body in raw.items():
        delivery.joinpath(i+'.json').write_bytes(body)
    assert load_raw_evidence(tmp_path)==raw
    delivery.joinpath(identity+'.json').write_bytes(raw[identity]+b' ')
    with pytest.raises(ValueError,match='conflicting'):
        load_raw_evidence(tmp_path)
