"""Collect separate Pendle PT datasets for research, without changing strategy."""
import argparse
import csv
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep

from .collect_pt_history import BACKEND, BASE_URL, parse_daily_closes
from app.adapters.ethereum_rpc import EthereumRpcClient, read_pendle_market_tokens
from app.adapters.pendle_api import PendleApiClient
from app.models.evidence import DataMode, EvidenceDTO, make_evidence_id
from app.models.timeseries import TimeseriesDTO
from app.storage.parquet_store import ParquetStore, observations_from_series
from app.storage.sqlite_store import SqliteStore


def main():
    import httpx
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--market', action='append', required=True)
    parser.add_argument('--days', type=int, default=180)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 66 <= args.days <= 1000:
        parser.error('days must be between 66 and 1000')
    catalog = {r['market'].lower(): r for r in json.loads(args.catalog.read_text())}
    selected = [catalog[address.lower()] for address in args.market]
    args.output.mkdir(parents=True, exist_ok=True)
    rpc = EthereumRpcClient()
    block = rpc.pin_block()
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    params = {'time_frame': 'day', 'timestamp_start': (end-timedelta(days=args.days)).isoformat(),
              'timestamp_end': end.isoformat()}
    reports = []
    with httpx.Client(timeout=60) as http, PendleApiClient() as pendle, SqliteStore() as sqlite:
        for market in selected:
            try:
                tokens, expiry_proof = read_pendle_market_tokens(rpc, market['market'], block)
                if tokens.pt != market['pt'].lower():
                    raise ValueError('PT identity mismatch')
                expiry = datetime.fromtimestamp(tokens.expiry_ts, timezone.utc)
                if expiry <= end+timedelta(days=7):
                    raise ValueError('expiry reached by forecast horizon')
                path = f'/v4/1/prices/{tokens.pt}/ohlcv'
                for attempt in range(3):
                    response = http.get(BASE_URL+path, params=params)
                    if response.status_code != 429:
                        break
                    sleep(min(30, float(response.headers.get('retry-after', '10'))))
                response.raise_for_status()
                retrieved = datetime.now(timezone.utc)
                ev_id = make_evidence_id('pendle_api', 'rest:/v4/prices/ohlcv',
                                         {**params, 'pt': tokens.pt}, fetched_at=retrieved)
                payload = response.json()
                points, excluded = parse_daily_closes(payload, available_by=end, evidence_id=ev_id)
                if not points:
                    raise ValueError('no completed daily closes')
                series = TimeseriesDTO.model_validate(dict(
                    chain_id=1, data_mode='REAL', series_id='ts_benchmark_'+tokens.pt[2:],
                    metric='MARKET_PRICE', subject_kind='ASSET', subject_id=tokens.pt,
                    unit='USD', pricing_method='API_SPOT', interval_seconds=86400,
                    source_id='pendle_api', status='OK', points=points))
                gaps = sum(b.timestamp-a.timestamp != timedelta(days=1)
                           for a,b in zip(series.points, series.points[1:]))
                raw_hash = hashlib.sha256(response.content).hexdigest()
                evidence = EvidenceDTO(chain_id=1, data_mode='REAL', evidence_id=ev_id,
                    source_kind='PENDLE_API', source_id='pendle_api', method='rest:/v4/prices/ohlcv',
                    request_params=params, target_address=tokens.pt, fetched_at=retrieved,
                    payload_sha256=raw_hash, status='OK')
                directory = args.output / tokens.pt
                directory.mkdir(parents=True, exist_ok=True)
                (directory/'raw_ohlcv.json').write_bytes(response.content)
                (directory/'series.json').write_text(series.model_dump_json(indent=2)+'\n')
                (directory/'evidence.json').write_text(evidence.model_dump_json(indent=2)+'\n')
                candles = list(csv.DictReader(io.StringIO(payload['results'])))
                completed = [r for r in candles if int(r['time'])+86400 <= int(end.timestamp())]
                with (directory/'daily_closes.csv').open('w', newline='') as f:
                    writer=csv.writer(f);writer.writerow(['ts_utc','close_usd','volume_reported'])
                    for r in sorted(completed,key=lambda r:int(r['time'])):
                        writer.writerow([datetime.fromtimestamp(int(r['time'])+86400,timezone.utc).isoformat(),
                                         r['close'],r['volume']])
                parquet=ParquetStore().write_observations(observations_from_series(
                    series, f'asset:1:{tokens.pt}', retrieved), DataMode.REAL, expected_interval_seconds=86400)
                report={**{k:v for k,v in market.items() if k!='pt_raw'},
                    'purpose':'SEPARATE_BENCHMARK_DATASET_NOT_CURRENT_STRATEGY',
                    'observations':len(points),'gaps':gaps,'expiry':expiry.isoformat(),
                    'first_observation_at':points[0]['timestamp'],'latest_observation_at':points[-1]['timestamp'],
                    'source_as_of':points[-1]['timestamp'],'retrieved_at':retrieved.isoformat(),
                    'source_snapshot_ref':'sha256:'+raw_hash,'source_url':str(response.url),
                    'excluded_incomplete_candles':excluded,'verified_block':block.number,
                    'pt_identity_evidence_id':tokens.ref.evidence_id,
                    'expiry_evidence_id':expiry_proof.evidence_id,'parquet_path':str(parquet),
                    'zero_reported_volume_days':sum(bool(r['volume']) and float(r['volume'])==0 for r in completed),
                    'missing_reported_volume_days':sum(not r['volume'] for r in completed),
                    'status':'READY_FOR_DATA_AUDIT' if len(points)>=66 and gaps==0 else 'INSUFFICIENT_HISTORY',
                    'warnings':['Retrospective provider closes; original availability/revisions not audited',
                                'Zero-volume provider quotes require review',
                                'Separate PT and expiry; never concatenate with current strategy',
                                'Sufficient length is not predictive validation']}
                (directory/'collection_report.json').write_text(json.dumps(report,indent=2)+'\n')
                # Extra yield history is kept raw, with its own provenance.
                try:
                    history=pendle.fetch_market_history(tokens.market_address, start=end-timedelta(days=args.days), end=end)
                    (directory/'raw_yield_history.json').write_bytes(history.raw_body)
                    (directory/'yield_evidence.json').write_text(history.to_evidence().model_dump_json(indent=2)+'\n')
                    sqlite.save_evidence([history.to_evidence()])
                except Exception as exc:
                    report['yield_history_error_type']=type(exc).__name__
                sqlite.save_evidence([evidence, *rpc.evidence.values()])
                reports.append(report)
                print(json.dumps({k:report[k] for k in ['name','pt','observations','gaps','first_observation_at',
                                                       'latest_observation_at','zero_reported_volume_days','status']}),flush=True)
            except Exception as exc:
                reports.append({'market':market['market'],'name':market['name'],
                                'status':'COLLECTION_ERROR','error_type':type(exc).__name__})
        (args.output/'rpc_evidence.json').write_text(json.dumps(
            [e.model_dump(mode='json') for e in rpc.evidence.values()],indent=2)+'\n')
    (args.output/'collection_summary.json').write_text(json.dumps(reports,indent=2)+'\n')
    return 1 if any(r['status'] == 'COLLECTION_ERROR' for r in reports) else 0


if __name__ == '__main__':
    raise SystemExit(main())
