"""Build an offline hackathon demo from verified stored research outputs."""
import argparse
from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path

from .run_comparable_benchmark import load_dataset


def make_payload(datasets, report, comparison):
    metadata = json.loads((datasets / 'collection_summary.json').read_text())
    splits = {r['pt']: r for r in json.loads((datasets / 'dataset_splits.json').read_text())}
    results = {r['pt']: r for r in report['results']}
    if report['protocol']['model_id'] != 'google/timesfm-2.5-200m-pytorch':
        raise ValueError('demo requires the recorded TimesFM report')
    if set(results) != {r['pt'] for r in metadata}:
        raise ValueError('report assets differ from datasets')
    assets = []
    for entry in metadata:
        series = load_dataset(datasets, entry, splits[entry['pt']])
        result = results[entry['pt']]
        if result['source_snapshot_ref'] != series.source_snapshot_ref:
            raise ValueError('forecast snapshot mismatch')
        forecast = result['forecast']
        if len(forecast) != 7:
            raise ValueError('forecast must contain exactly seven days')
        for h, point in enumerate(forecast, start=1):
            if (point['horizon_days'] != h or datetime.fromisoformat(point['timestamp']) !=
                    series.timestamps[-1] + timedelta(days=h)):
                raise ValueError('forecast is not aligned with the last observation')
            q = [Decimal(point[k]) for k in ('q10', 'q50', 'q90')]
            if any(not v.is_finite() or v <= 0 for v in q) or not q[0] <= q[1] <= q[2]:
                raise ValueError('invalid forecast quantiles')
        assets.append(dict(name=entry['name'], pt=entry['pt'], market=entry['market'],
            expiry=entry['expiry'], snapshot=series.source_snapshot_ref,
            retrieved_at=entry['retrieved_at'], source_url=entry['source_url'],
            history=[dict(timestamp=t.isoformat(), value=str(v))
                     for t, v in zip(series.timestamps, series.values)],
            forecast=forecast, test=result['final_test'], warnings=entry['warnings']))
    candidate = next(m for m in comparison['models'] if m['model_id'] == report['protocol']['model_id'])
    for h, metric in zip((1, 3, 7), report['pooled_final_test_metrics']):
        compared = next(m for m in candidate['test'] if m['horizon_days'] == h)
        if compared['mae_model'] != metric['mae_model']:
            raise ValueError('comparison is from another run')
    return dict(status='RESEARCH_ONLY', as_of=assets[0]['history'][-1]['timestamp'],
        model_id=report['protocol']['model_id'], revision=report['protocol']['revision'],
        assets=assets, comparison=comparison, runtime=report['runtime'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--datasets', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--comparison', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    payload = make_payload(args.datasets, json.loads(args.report.read_text()),
                           json.loads(args.comparison.read_text()))
    template = (Path(__file__).parent / 'templates/marilou_demo.html').read_text()
    encoded = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'index.html').write_text(template.replace('__MARILOU_PAYLOAD__', encoded))
    (args.output / 'demo-data.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    pitch = Path(__file__).resolve().parents[1] / 'docs/demo/marilou-pitch.md'
    (args.output / 'PITCH.md').write_text(pitch.read_text())
    (args.output / 'LIRE_MOI.txt').write_text(
        'MARILOU — DEMO BSA\n\n'
        'Ouvrir index.html dans Safari, Chrome ou le navigateur de votre choix.\n'
        'La démo fonctionne hors ligne et ne nécessite aucune clé API.\n'
        'Lire PITCH.md pour le texte et le parcours de présentation.\n'
        'demo-data.json contient les données publiques embarquées et leurs références.\n'
        'Snapshot du 10 octobre 2026 ; prototype expérimental, pas une validation indépendante.\n')
    print(args.output / 'index.html')


if __name__ == '__main__':
    main()
