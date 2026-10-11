"""Reproducible offline API validation on an archived REAL market snapshot.

An incomplete oracle window stays UNAVAILABLE. Optional assumed volatility runs
are functional checks on real market inputs, never observed calibration validation.
No network, wallet operations, store mutation or synthetic fixture fallback.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.analytics.dataset import AnalyticsSnapshot, load_snapshot
from app.main import create_app
from app.stress.monte_carlo.calibration import (MonteCarloDataUnavailable,
    calibrate_oracle_volatility, select_oracle_series, sampling_targets)


def validate(snapshot, strategy_id, position_id, *, assumed_volatility=None, num_paths=10000):
    position = snapshot.position(strategy_id,position_id)
    market = snapshot.market(position)
    if market.data_mode.value != 'REAL' or position.data_mode.value != 'REAL':
        raise ValueError('REAL Data inputs required; no DEMO substitution')
    if assumed_volatility is not None and (not assumed_volatility.is_finite() or not 0<=assumed_volatility<=5):
        raise ValueError('assumed volatility must be between 0 and 5')
    series, missing = select_oracle_series(snapshot.series,market)
    calibration = None
    try:
        if missing:
            raise MonteCarloDataUnavailable(missing)
        calibration = calibrate_oracle_volatility(series,market,evidence=snapshot.evidence,as_of=snapshot.as_of)
    except MonteCarloDataUnavailable as exc:
        missing=exc.missing_fields
    report = dict(schema_version='monte-carlo-real-validation-v1',
        executed_at=datetime.now(timezone.utc).isoformat(),snapshot_as_of=snapshot.as_of.isoformat(),
        snapshot_sha256=hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest(),
        strategy_id=strategy_id,position_id=position_id,position_kind=position.kind.value,
        market_id=market.market_id,oracle_address=market.morpho.oracle_address,
        observed_calibration_status='CALIBRATED' if calibration else 'UNAVAILABLE',
        required_daily_samples=31,
        missing_fields=missing,calibration=calibration.model_dump(mode='json') if calibration else None,
        history=series.model_dump(mode='json') if series else None,runs=[],checks={},
        limitations=['Archived replay, not live execution.',
            'Simulated funds remain hypothetical; REAL_DATA requires a verified on-chain position.',
            'Breach frequency is model-implied, not an objective liquidation probability.',
            'Losses are oracle mark-to-model amounts in loan-token units, not realized losses or USD.'])
    report['missing_daily_samples']=sum(name.startswith('oracle_series.sample:') for name in missing)
    report['available_daily_samples']=(31-report['missing_daily_samples']) if (
        calibration is not None or missing and all(name.startswith('oracle_series.sample:') for name in missing)) else 0
    settings = dict(strategy_id=strategy_id,position_id=position_id,num_paths=num_paths,seed=42,
                    volatility_source='OBSERVED_ORACLE_SERIES')
    with TestClient(create_app(snapshot=snapshot,replay=True)) as api:
        for horizon in (1,3,7):
            response=api.post('/api/v1/monte-carlo',json={**settings,'horizon_days':horizon})
            if response.status_code!=200:
                raise ValueError('observed API request failed: '+response.text)
            result=response.json()['data']
            report['runs'].append(dict(source='OBSERVED_ORACLE_SERIES',horizon_days=horizon,result=result))
        if assumed_volatility is not None:
            settings={**settings,'volatility_source':'USER_ASSUMPTION','annual_volatility':str(assumed_volatility)}
            for horizon in (1,3,7):
                body={**settings,'horizon_days':horizon}
                first=api.post('/api/v1/monte-carlo',json=body)
                second=api.post('/api/v1/monte-carlo',json=body)
                if first.status_code!=200 or second.status_code!=200:
                    raise ValueError('assumption API request failed')
                result=first.json()['data']
                report['runs'].append(dict(source='USER_ASSUMPTION',horizon_days=horizon,
                    annual_volatility_assumption=str(assumed_volatility),result=result))
                report['checks']['seed_reproducible_j'+str(horizon)]=first.json()==second.json()
                report['checks']['assumption_scope_hypothetical_j'+str(horizon)]=(result['status']=='OK'
                    and result['data'] is not None and 'HYPOTHETICAL_INPUT_SCOPE' in result['warnings']
                    and result['provenance']['calibration']=='USER_ASSUMPTION')
    report['checks']['missing_history_not_substituted']=all(
        r['result']['status']=='UNAVAILABLE' and r['result']['data'] is None for r in report['runs']
        if r['source']=='OBSERVED_ORACLE_SERIES') if calibration is None else True
    # A stale real snapshot must not run, even when volatility is supplied.
    control={**settings,'horizon_days':7}
    with TestClient(create_app(snapshot=snapshot,replay=False,clock=lambda:snapshot.as_of+timedelta(minutes=6))) as api:
        stale=api.post('/api/v1/monte-carlo',json=control).json()['data']
        report['checks']['stale_inputs_refused']=stale['status']=='UNAVAILABLE' and stale['data'] is None
    data=snapshot.model_dump()
    index=next(i for i,m in enumerate(snapshot.markets) if m.market_key==market.market_key)
    oracle=data['markets'][index]['morpho']['oracle_price']
    oracle['raw_value']*=2
    oracle['value']*=2
    tampered=AnalyticsSnapshot.model_validate(data)
    with TestClient(create_app(snapshot=tampered,replay=True)) as api:
        rejected=api.post('/api/v1/monte-carlo',json=control).json()['data']
        report['checks']['oracle_proof_mismatch_refused']=rejected['status']=='UNAVAILABLE' and 'oracle.rpc_evidence' in rejected['missing_fields']
    observed_success=calibration is not None and all(r['result']['status']=='OK' and r['result']['data'] is not None
        for r in report['runs'] if r['source']=='OBSERVED_ORACLE_SERIES')
    report['status']='OBSERVED_PIPELINE_CHECKS_PASSED' if observed_success and all(report['checks'].values()) else 'PARTIAL_VALIDATION'
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',required=True,type=Path)
    parser.add_argument('--strategy-id',required=True)
    parser.add_argument('--position-id',required=True)
    parser.add_argument('--assumed-volatility',type=Decimal)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    report=validate(load_snapshot(args.snapshot),args.strategy_id,args.position_id,
                    assumed_volatility=args.assumed_volatility)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as handle:
        handle.write(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','available_daily_samples','missing_daily_samples','checks')}))
    return 0 if all(report['checks'].values()) else 1


if __name__=='__main__':
    raise SystemExit(main())
