"""Separate PT research benchmarks with a frozen, rolling 14-day final test."""
import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from time import perf_counter

from .collect_pt_history import parse_daily_closes
from backend.app.models.timeseries import TimeseriesDTO
from .benchmark import HORIZONS, persistence, rolling_mean
from .chronos2 import Chronos2Forecaster, evaluate
from .series import DailySeries


def summarize(rows):
    metrics = []
    for h in HORIZONS:
        window = [r for r in rows if r['horizon_days'] == h]
        count = len(window)
        errors = {name: [Decimal(r[name])-Decimal(r['actual']) for r in window]
                  for name in ('q50', 'naive', 'rolling_mean')}
        mae = {name: sum(abs(e) for e in es)/count for name, es in errors.items()}
        metrics.append(dict(horizon_days=h, n_evaluations=count,
            mae_model=str(mae['q50']), mae_naive=str(mae['naive']),
            mae_rolling_mean=str(mae['rolling_mean']),
            rmse_model=str((sum(e*e for e in errors['q50'])/count).sqrt()),
            coverage_q10_q90=str(Decimal(sum(Decimal(r['q10']) <= Decimal(r['actual']) <=
                Decimal(r['q90']) for r in window))/count),
            beats_naive=mae['q50'] < mae['naive'],
            beats_rolling_mean=mae['q50'] < mae['rolling_mean'],
            gain_vs_naive_percent=str(100*(1-mae['q50']/mae['naive'])) if mae['naive'] else None))
    return metrics


def final_test(series, model):
    """Two non-overlapping weeks; second origin sees the first week's observations."""
    n = len(series.values)
    if n < 80:
        raise ValueError('need >=66 development observations and 14 reserved test observations')
    rows = []
    for origin in (n-14, n-7):
        prediction = model.predict(series, context_length=origin)
        context = series.values[:origin]
        naive, mean = persistence(context, 7), rolling_mean(context, 7)
        for h in HORIZONS:
            point = prediction.days[h-1]
            rows.append(dict(cutoff_at=series.timestamps[origin-1].isoformat(),
                context_observations=origin, horizon_days=h, timestamp=point.timestamp.isoformat(),
                actual=str(series.values[origin+h-1]), q10=str(point.q10), q50=str(point.q50),
                q90=str(point.q90), naive=str(naive[h-1]), rolling_mean=str(mean[h-1])))
    return dict(n_origins=2, windows=rows, horizon_metrics=summarize(rows))


def load_dataset(root, report, split):
    directory = root/report['pt']
    dto = TimeseriesDTO.model_validate_json((directory/'series.json').read_text())
    raw = (directory/'raw_ohlcv.json').read_bytes()
    digest = 'sha256:'+hashlib.sha256(raw).hexdigest()
    if digest != report['source_snapshot_ref'] or digest != split['source_snapshot_ref']:
        raise ValueError('snapshot identity mismatch')
    evidence = json.loads((directory/'evidence.json').read_text())
    points, _ = parse_daily_closes(json.loads(raw), available_by=datetime.fromisoformat(report['source_as_of']),
                                   evidence_id=evidence['evidence_id'])
    if [(datetime.fromisoformat(p['timestamp']), Decimal(p['value'])) for p in points] != [
            (p.timestamp,p.value) for p in dto.points]:
        raise ValueError('normalized data differs from raw snapshot')
    if (dto.chain_id != 1 or dto.data_mode.value != 'REAL' or dto.subject_id != report['pt'] or
            dto.metric.value != 'MARKET_PRICE' or dto.unit.value != 'USD' or dto.interval_seconds != 86400):
        raise ValueError('wrong benchmark asset, unit or frequency')
    if any(p.status.value != 'OK' or p.evidence_ids != (evidence['evidence_id'],) for p in dto.points):
        raise ValueError('invalid observation evidence')
    if len(points) != report['observations'] or len(points)-14 != split['development_observations']:
        raise ValueError('split counts differ')
    if dto.points[-15].timestamp.isoformat() != split['development_last_observation_at']:
        raise ValueError('development cutoff differs')
    rpc = {e['evidence_id']:e for e in json.loads((root/'rpc_evidence.json').read_text())}
    token_proof, expiry_proof = rpc[report['pt_identity_evidence_id']], rpc[report['expiry_evidence_id']]
    expiry = datetime.fromisoformat(report['expiry'])
    if (token_proof['status'] != 'OK' or token_proof['target_address'] != report['market'] or
            token_proof['decoded_excerpt']['_PT'].lower() != dto.subject_id or
            expiry_proof['status'] != 'OK' or expiry_proof['target_address'] != dto.subject_id or
            int(expiry_proof['decoded_excerpt']['out0']) != int(expiry.timestamp())):
        raise ValueError('RPC identity or expiry proof mismatch')
    return DailySeries(strategy_id='benchmark:'+dto.subject_id, entity_id='pendle:1:'+report['market'],
        timestamps=tuple(p.timestamp for p in dto.points), values=tuple(p.value for p in dto.points),
        expiry=expiry, source_snapshot_ref=digest, source_as_of=dto.points[-1].timestamp,
        retrieved_at=datetime.fromisoformat(report['retrieved_at']),
        evidence_ids=(evidence['evidence_id'],token_proof['evidence_id'],expiry_proof['evidence_id']),
        data_mode=dto.data_mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--datasets', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--model', choices=['chronos2', 'bolt', 'timesfm'], default='chronos2')
    args = parser.parse_args()
    reports = json.loads((args.datasets/'collection_summary.json').read_text())
    splits = {s['pt']:s for s in json.loads((args.datasets/'dataset_splits.json').read_text())}
    series = [load_dataset(args.datasets,r,splits[r['pt']]) for r in reports]
    args.output.mkdir(parents=True, exist_ok=False)
    from .pretrained_candidates import MODEL_IDS, TIMESFM_CONFIG, load_candidate
    protocol = dict(model_id=MODEL_IDS[args.model], revision=args.revision, seed=42, device='cpu',
        min_development_context=45, horizon_days=list(HORIZONS), test_days=14,
        test_protocol='two weekly rolling origins; actual week one available to second origin',
        weights_updated=False, tuning=False, assets=[r['pt'] for r in reports],
        recorded_at=datetime.now(timezone.utc).isoformat(),
        dataset_splits_sha256=hashlib.sha256((args.datasets/'dataset_splits.json').read_bytes()).hexdigest())
    protocol['comparison_status'] = 'EXPLORATORY_PREVIOUSLY_CONSULTED_TEST'
    if args.model == 'bolt':
        protocol['quantile_postprocessing'] = 'q10=min(q10,q50); q90=max(q90,q50); median unchanged'
    if args.model == 'timesfm':
        protocol['forecast_config'] = TIMESFM_CONFIG
    (args.output/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    import torch
    torch.set_num_threads(4)
    started = perf_counter()
    model = load_candidate(args.model, revision=args.revision)
    loaded = perf_counter()-started
    print('Model loaded; frozen four-asset protocol, no tuning',flush=True)
    results, all_test = [], []
    for metadata, data in zip(reports, series):
        dev_count = len(data.values)-14
        development = replace(data, timestamps=data.timestamps[:dev_count], values=data.values[:dev_count],
                              source_as_of=data.timestamps[dev_count-1])
        dev = evaluate(development, model)
        dev['strategy_id'] = None
        test = final_test(data, model)
        latest = model.predict(data)
        forecast = [dict(timestamp=p.timestamp.isoformat(), horizon_days=h,
                         q10=str(p.q10),q50=str(p.q50),q90=str(p.q90))
                    for h,p in enumerate(latest.days,start=1)]
        result = dict(name=metadata['name'],pt=metadata['pt'],market=metadata['market'],
            status='RESEARCH_ONLY',n_observations=len(data.values),development=dev,
            final_test=test,forecast=forecast,source_evidence_ids=list(data.evidence_ids),
            source_snapshot_ref=data.source_snapshot_ref,source_warnings=metadata['warnings'])
        (args.output/(metadata['pt']+'.json')).write_text(json.dumps(result,indent=2)+'\n')
        results.append(result)
        all_test.extend(dict(row,pt=metadata['pt']) for row in test['windows'])
        print(json.dumps({'name':metadata['name'],'development_windows':dev['n_origins'],
                          'final_test_metrics':test['horizon_metrics']}),flush=True)
    summary = dict(status='RESEARCH_ONLY',results=results,pooled_final_test_metrics=summarize(all_test),
        protocol=protocol,model_load_seconds=loaded,total_seconds=perf_counter()-started,
        runtime={n:version(n) for n in ['chronos-forecasting','torch','transformers','pandas'] +
                 (['timesfm'] if args.model == 'timesfm' else [])},
        limitations=['Four separate assets; cannot validate original PT-siUSD forecast',
                     'Only two held-out weeks per asset, overlapping calendar dates: correlated errors',
                     'Provider availability/revisions and possible pretraining overlap not established',
                     'Previously consulted final test: exploratory model comparison, no independent validation',
                     'No fine-tuning, financial decision cache or validated forecast'])
    summary['outer_quantile_corrected_rows'] = getattr(model.pipeline, 'corrected_rows', 0)
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({'total_seconds':summary['total_seconds'],
                      'pooled_final_test_metrics':summary['pooled_final_test_metrics']}),flush=True)


if __name__ == '__main__':
    main()
