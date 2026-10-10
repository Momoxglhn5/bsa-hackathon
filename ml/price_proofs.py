"""Bind ML price inputs to hashed provider responses, not arbitrary evidence IDs."""
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal

from app.models.timeseries import TimeseriesDTO
from .collect_pt_history import parse_daily_closes


def raw_price_proof(proof, raw_evidence, deadline, *, method):
    if (proof is None or proof.data_mode.value != 'REAL' or proof.chain_id != 1
            or proof.status.value != 'OK' or proof.source_kind.value != 'PENDLE_API'
            or proof.source_id != 'pendle_api' or proof.method != method
            or proof.fetched_at > deadline or not proof.payload_sha256):
        raise ValueError('invalid price evidence source, method or availability')
    raw = raw_evidence.get(proof.evidence_id)
    if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != proof.payload_sha256:
        raise ValueError('price evidence raw payload missing or hash mismatch')
    return json.loads(raw, parse_float=Decimal)


def verify_series_prices(series, pt, evidence, raw_evidence, deadline):
    series = TimeseriesDTO.model_validate(series.model_dump())
    if (series.data_mode.value != 'REAL' or series.chain_id != 1 or series.subject_id != pt
            or series.subject_kind.value != 'ASSET' or series.metric.value != 'MARKET_PRICE'
            or series.unit.value != 'USD' or series.interval_seconds != 86400
            or series.source_id != 'pendle_api' or series.status.value != 'OK'
            or series.pricing_method.value != 'API_SPOT'):
        raise ValueError('price evidence requires daily Pendle PT USD observations')
    parsed = {}
    for point in series.points:
        if point.status.value != 'OK' or point.value is None or not point.evidence_ids:
            raise ValueError('price evidence missing from observation')
        for identity in point.evidence_ids:
            if identity not in parsed:
                proof = evidence.get(identity)
                payload = raw_price_proof(proof, raw_evidence, deadline, method='rest:/v4/prices/ohlcv')
                if proof.target_address != pt or proof.request_params.get('time_frame') != 'day':
                    raise ValueError('price evidence refers to another asset or frequency')
                try:
                    start = datetime.fromisoformat(proof.request_params['timestamp_start'])
                    end = datetime.fromisoformat(proof.request_params['timestamp_end'])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError('price evidence request dates missing or invalid') from exc
                if start.tzinfo is None or end.tzinfo is None or start >= end or end > proof.fetched_at:
                    raise ValueError('price evidence request dates invalid')
                rows, _ = parse_daily_closes(payload, available_by=proof.fetched_at, evidence_id=identity)
                parsed[identity] = {datetime.fromisoformat(r['timestamp']): Decimal(r['value']) for r in rows
                                    if start <= datetime.fromisoformat(r['timestamp'])-timedelta(days=1)
                                    and datetime.fromisoformat(r['timestamp']) <= end}
            if parsed[identity].get(point.timestamp) != point.value:
                raise ValueError('price evidence observation date or value mismatch')
    return series


def verify_spot_price(price, asset, evidence, raw_evidence, now):
    if (price.base_asset.address != asset.address or price.quote_unit.value != 'USD'
            or price.pricing_method.value != 'API_SPOT' or not price.evidence_ids):
        raise ValueError('spot price evidence asset or unit mismatch')
    key = '1-'+asset.address
    for identity in price.evidence_ids:
        proof = evidence.get(identity)
        payload = raw_price_proof(proof, raw_evidence, now, method='rest:/v1/prices/assets')
        if (proof.request_params.get('chainId') != '1'
                or key not in proof.request_params.get('ids','').split(',')
                or price.observed_at != proof.fetched_at
                or Decimal(str(payload.get('prices',{}).get(key))) != price.value):
            raise ValueError('spot price evidence source, asset, date or value mismatch')
