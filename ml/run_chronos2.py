"""Run reproducible offline research; never publish to the application cache."""
import argparse
import hashlib
import json
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

from backend.app.models.strategy import StrategyDTO
from backend.app.models.timeseries import TimeseriesDTO
from .chronos2 import Chronos2Forecaster, evaluate
from .series import prepare_pt_series


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', type=Path, required=True)
    parser.add_argument('--strategy', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--device', choices=['auto', 'cpu', 'cuda'], default='cpu')
    parser.add_argument('--exploratory', action='store_true')
    args = parser.parse_args()
    collection = json.loads((args.export / 'collection_report.json').read_text())
    digest = hashlib.sha256((args.export / 'raw_ohlcv.json').read_bytes()).hexdigest()
    if collection['source_snapshot_ref'] != 'sha256:' + digest:
        raise ValueError('raw input hash does not match collection report')
    strategy = StrategyDTO.model_validate_json(args.strategy.read_text())
    series = prepare_pt_series(
        TimeseriesDTO.model_validate_json((args.export / 'series.json').read_text()),
        strategy,
        source_snapshot_ref=collection['source_snapshot_ref'],
        source_as_of=datetime.fromisoformat(collection['source_as_of']),
        retrieved_at=datetime.fromisoformat(collection['retrieved_at']),
        min_observations=14 if args.exploratory else 45,
        exploratory=args.exploratory,
    )
    import torch
    torch.set_num_threads(4)
    started = perf_counter()
    print(f'Loading pinned Chronos-2 on {args.device}; {len(series.values)} observations', flush=True)
    model = Chronos2Forecaster.load(revision=args.revision, device=args.device)
    load_seconds = perf_counter() - started
    print('Model loaded; starting chronological backtest', flush=True)
    report = evaluate(series, model, exploratory=args.exploratory)
    report['runtime'] = {name: version(name) for name in
                         ('chronos-forecasting', 'torch', 'transformers', 'pandas', 'huggingface-hub')}
    report['runtime'].update(device=str(model.pipeline.model.device), seed=42,
                             torch_threads=torch.get_num_threads())
    report['model_load_seconds'] = load_seconds
    report['total_seconds'] = perf_counter() - started
    report['weights_updated'] = False
    report['input_path'] = str(args.export.resolve())
    report['source_evidence_ids'] = list(series.evidence_ids)
    report['limitations'].extend(collection['warnings'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'output': str(args.output), 'status': report['status'],
                      'n_origins': report['n_origins'],
                      'horizon_metrics': report['horizon_metrics'],
                      'forecast': report['forecast'],
                      'total_seconds': report['total_seconds']}, indent=2), flush=True)


if __name__ == '__main__':
    main()
