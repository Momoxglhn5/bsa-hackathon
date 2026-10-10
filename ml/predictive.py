"""Prospective, asset-specific validation and conditional decision projections.

Run with PYTHONPATH=backend python -m ml.predictive. No inference in the API.
Files are append-only through the CLI; timestamps use the runtime UTC clock.
"""
import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from app.analytics.dataset import load_snapshot
from app.analytics.valuation import value_position
from app.decision.forecast import ForecastBundle, projection_fingerprint
from app.models.evidence import (ContractModel, EvidenceDTO, EvidenceId,
                                 PositiveDecimal, UnitRatio, UtcDatetime)
from app.models.timeseries import TimeseriesDTO
from .series import prepare_pt_series
from .price_proofs import verify_series_prices, verify_spot_price


class Protocol(ContractModel):
    version: Literal['marilou-prospective-v1'] = 'marilou-prospective-v1'
    frozen_at: UtcDatetime
    strategy_id: str
    entity_id: str = Field(pattern=r'^pendle:1:0x[0-9a-f]{40}$')
    model: Literal['timesfm', 'chronos2', 'bolt'] = 'timesfm'
    revision: str = Field(pattern=r'^[0-9a-f]{40}$')
    runtime_package_version: str = Field(min_length=1)
    configuration_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    minimum_windows: int = Field(default=12, ge=12, le=1000)
    minimum_gain: UnitRatio = Decimal('0.05')
    coverage_min: UnitRatio = Decimal('0.70')
    coverage_max: UnitRatio = Decimal('0.90')

    @model_validator(mode='after')
    def ordered(self):
        if self.coverage_min > self.coverage_max:
            raise ValueError('coverage bounds reversed')
        return self


def digest(obj):
    return hashlib.sha256(obj.model_dump_json().encode()).hexdigest()


def configuration_hash(model):
    from .pretrained_candidates import TIMESFM_CONFIG
    adapter_files = ('pretrained_candidates.py', 'chronos2.py')
    config = {'model': model, 'seed': 42, 'device': 'cpu',
              'timesfm': TIMESFM_CONFIG if model == 'timesfm' else None,
              'adapters': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                           for name in adapter_files}}
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


class PricePoint(ContractModel):
    timestamp: UtcDatetime
    q10: PositiveDecimal
    q50: PositiveDecimal
    q90: PositiveDecimal

    @model_validator(mode='after')
    def ordered(self):
        if not self.q10 <= self.q50 <= self.q90:
            raise ValueError('unordered quantiles')
        return self


class IssuedForecast(ContractModel):
    protocol_sha256: str
    issued_at: UtcDatetime
    cutoff_at: UtcDatetime
    source_snapshot_ref: str
    source_evidence_ids: tuple[EvidenceId, ...] = Field(min_length=1)
    last_price: PositiveDecimal
    rolling_mean: PositiveDecimal
    points: tuple[PricePoint, ...]
    context_series: TimeseriesDTO

    @model_validator(mode='after')
    def chronology(self):
        if self.cutoff_at > self.issued_at or self.issued_at-self.cutoff_at > timedelta(days=1):
            raise ValueError('future or stale context')
        if len(self.points) != 7:
            raise ValueError('seven daily quantiles required')
        for h, point in enumerate(self.points, 1):
            if point.timestamp != self.cutoff_at+timedelta(days=h) or point.timestamp <= self.issued_at:
                raise ValueError('forecasts must be issued before every target')
        return self


def utc_now():
    return datetime.now(timezone.utc)


def issue(protocol, series, model, *, observations, evidence, raw_evidence, clock=utc_now):
    now = clock()
    if (series.data_mode.value != 'REAL' or series.strategy_id != protocol.strategy_id
            or series.entity_id != protocol.entity_id or now < protocol.frozen_at):
        raise ValueError('REAL strategy, asset and frozen protocol must match')
    from .pretrained_candidates import MODEL_IDS
    package = 'timesfm' if protocol.model == 'timesfm' else 'chronos-forecasting'
    expected_version = package+'='+protocol.runtime_package_version+';model='+protocol.revision
    if (model.model_id != MODEL_IDS[protocol.model] or model.model_version != expected_version
            or protocol.configuration_sha256 != configuration_hash(protocol.model)):
        raise ValueError('model does not match frozen revision')
    if series.retrieved_at > now:
        raise ValueError('source retrieved in the future')
    verify_series_prices(observations, observations.subject_id, evidence, raw_evidence, now)
    if (tuple(p.timestamp for p in observations.points) != series.timestamps
            or tuple(p.value for p in observations.points) != series.values
            or {i for p in observations.points for i in p.evidence_ids} != set(series.evidence_ids)
            or 'sha256:'+digest(observations) != series.source_snapshot_ref):
        raise ValueError('model context differs from proven observations')
    prediction = model.predict(series)
    issued_at = clock()
    if issued_at < now:
        raise ValueError('issuance clock moved backwards')
    return IssuedForecast(protocol_sha256=digest(protocol), issued_at=issued_at,
        cutoff_at=series.timestamps[-1], source_snapshot_ref=series.source_snapshot_ref,
        source_evidence_ids=series.evidence_ids, last_price=series.values[-1],
        rolling_mean=sum(series.values[-7:])/7, context_series=observations,
        points=tuple(PricePoint(timestamp=p.timestamp, q10=p.q10, q50=p.q50, q90=p.q90)
                     for p in prediction.days))


def evaluate(protocol, forecasts, observations, strategy, now, *, evidence, raw_evidence):
    """No pooling across assets/horizons; only completed non-overlapping origins."""
    prepare_pt_series(observations, strategy, source_snapshot_ref='prospective-evaluation',
                      source_as_of=now, retrieved_at=now, min_observations=14, exploratory=True)
    if (strategy.data_mode.value != 'REAL' or strategy.strategy_id != protocol.strategy_id
            or 'pendle:1:'+strategy.pendle_market_address != protocol.entity_id):
        raise ValueError('validation target mismatch')
    verify_series_prices(observations, strategy.pt.address, evidence, raw_evidence, now)
    forecasts = sorted(forecasts, key=lambda f: f.cutoff_at)
    actuals = {p.timestamp: p for p in observations.points}
    previous = None
    rows = {h: [] for h in (1, 3, 7)}
    for forecast in forecasts:
        forecast = IssuedForecast.model_validate(forecast.model_dump())
        if (forecast.protocol_sha256 != digest(protocol) or forecast.issued_at < protocol.frozen_at
                or forecast.issued_at > now):
            raise ValueError('forecast is outside frozen prospective protocol')
        context = verify_series_prices(forecast.context_series, strategy.pt.address,
                                       evidence, raw_evidence, forecast.issued_at)
        if any(p.timestamp in actuals and actuals[p.timestamp].value != p.value for p in context.points):
            raise ValueError('source revision detected in forecast context')
        context_ids = {i for p in context.points for i in p.evidence_ids}
        if (len(context.points) < 45 or context.points[-1].timestamp != forecast.cutoff_at
                or context.points[-1].value != forecast.last_price
                or sum(p.value for p in context.points[-7:])/7 != forecast.rolling_mean
                or context_ids != set(forecast.source_evidence_ids)
                or forecast.source_snapshot_ref != 'sha256:'+digest(context)
                or any(b.timestamp-a.timestamp != timedelta(days=1)
                       for a,b in zip(context.points,context.points[1:]))):
            raise ValueError('forecast baselines or context differ from proven prices')
        known_cutoff = actuals.get(forecast.cutoff_at)
        if known_cutoff is not None and known_cutoff.value != forecast.last_price:
            raise ValueError('source revision detected at forecast cutoff')
        if previous is not None and forecast.cutoff_at-previous < timedelta(days=7):
            raise ValueError('overlapping or duplicate weekly origins')
        previous = forecast.cutoff_at
        for h in rows:
            point = forecast.points[h-1]
            observed = actuals.get(point.timestamp)
            if observed is None or point.timestamp > now:
                continue
            rows[h].append((point, observed))
    scores = []
    for h, pairs in rows.items():
        count = len(pairs)
        # Associate each point with its original baselines, never a later observation.
        baselines = {f.points[h-1].timestamp: f for f in forecasts}
        reasons = []
        metrics = {}
        if count:
            mae = sum(abs(p.q50-o.value) for p, o in pairs)/count
            naive = sum(abs(baselines[p.timestamp].last_price-o.value) for p, o in pairs)/count
            mean = sum(abs(baselines[p.timestamp].rolling_mean-o.value) for p, o in pairs)/count
            coverage = Decimal(sum(p.q10 <= o.value <= p.q90 for p, o in pairs))/count
            metrics = dict(mae_model=str(mae), mae_naive=str(naive), mae_rolling_mean=str(mean),
                coverage_q10_q90=str(coverage),
                rmse_model=str((sum((p.q50-o.value)**2 for p, o in pairs)/count).sqrt()))
            if naive == 0 or mae > naive*(1-protocol.minimum_gain) or mae >= naive:
                reasons.append('NO_GAIN_VS_PERSISTENCE')
            if mae >= mean:
                reasons.append('NO_GAIN_VS_ROLLING_MEAN')
            if not protocol.coverage_min <= coverage <= protocol.coverage_max:
                reasons.append('INTERVAL_COVERAGE_OUTSIDE_POLICY')
        if count < protocol.minimum_windows:
            reasons.append('INSUFFICIENT_PROSPECTIVE_WINDOWS')
        scores.append(dict(horizon_days=h, n_evaluations=count, metrics=metrics,
                           status='VALIDATED' if not reasons else 'NOT_VALIDATED', reasons=reasons))
    return dict(protocol_sha256=digest(protocol), evaluated_at=now.isoformat(),
        strategy_id=protocol.strategy_id, entity_id=protocol.entity_id, horizons=scores,
        forecast_hashes=[digest(f) for f in forecasts],
        observations_sha256=digest(observations),
        limitations=['Policy-qualified prospective test, not a guarantee or probability calibration.',
                     'Local timestamps and evidence require source audit; files are not signed attestations.',
                     'Pretraining overlap remains unknown. No oracle or hack prediction.'])


def project(protocol, forecasts, observations, snapshot, position_id, annual_borrow_apr, now, *, raw_evidence):
    """Recompute eligibility; never trust a manually marked VALIDATED report.

    NET_CARRY is expected PT mark-to-market change minus simple borrowing cost.
    No extra PT yield: that would double count the predicted price appreciation.
    Loan USD price, amounts and APR are frozen conditional assumptions.
    """
    evidence_index = {e.evidence_id: e for e in snapshot.evidence}
    report = evaluate(protocol, forecasts, observations, snapshot.strategy(protocol.strategy_id), now,
                      evidence=evidence_index, raw_evidence=raw_evidence)
    latest = max(forecasts, key=lambda f: f.issued_at)
    position = snapshot.position(protocol.strategy_id, position_id)
    market = snapshot.market(position)
    if market.data_mode.value != 'REAL' or now-latest.issued_at > timedelta(days=1):
        raise ValueError('REAL market and fresh forecast required')
    if now-latest.cutoff_at > timedelta(minutes=5):
        raise ValueError('forecast horizon must align with current decision within five minutes')
    if position.as_of > now or now-position.as_of > timedelta(minutes=5):
        raise ValueError('position is stale')
    if annual_borrow_apr < 0 or not annual_borrow_apr.is_finite():
        raise ValueError('finite nonnegative annual APR required')
    valuation = value_position(snapshot, position, now=now)
    if valuation.equity_usd is None:
        raise ValueError('fresh proven USD prices and amounts required')
    verify_spot_price(market.morpho.collateral_price, position.collateral_asset, evidence_index, raw_evidence, now)
    verify_spot_price(market.morpho.loan_price, position.loan_asset, evidence_index, raw_evidence, now)
    ids = tuple(sorted({i for f in forecasts for i in f.source_evidence_ids} |
                       {i for p in observations.points for i in p.evidence_ids} |
                       set(market.morpho.collateral_price.evidence_ids) |
                       set(market.morpho.loan_price.evidence_ids) | set(position.evidence_ids)))
    if any(i not in evidence_index or evidence_index[i].status != 'OK'
           or evidence_index[i].data_mode.value != 'REAL' or evidence_index[i].fetched_at > now for i in ids):
        raise ValueError('missing or invalid REAL source evidence')
    payload = json.dumps(report, sort_keys=True)
    report_hash = hashlib.sha256(payload.encode()).hexdigest()
    proof = EvidenceDTO(evidence_id='ev_'+report_hash[:24], data_mode='REAL', chain_id=1,
        source_kind='DERIVED', source_id='marilou_validation', method='prospective_validation_v1',
        fetched_at=now, payload_sha256=report_hash, status='OK',
        decoded_excerpt={'report_sha256': report_hash, 'annual_borrow_apr_assumption': str(annual_borrow_apr)})
    bundles = []
    for score in report['horizons']:
        if score['status'] != 'VALIDATED':
            continue
        h = score['horizon_days']
        point = latest.points[h-1]
        if point.timestamp <= now:
            continue
        with localcontext() as ctx:
            ctx.prec = 60
            carry = (position.collateral_amount*(point.q50-market.morpho.collateral_price.value)
                     - valuation.debt_value_usd*annual_borrow_apr*Decimal(h)/365)
        bundles.append(ForecastBundle(bundle_id='marilou:'+report_hash+':'+position_id+':'+str(h),
            strategy_id=protocol.strategy_id, position_id=position_id, horizon_days=h,
            generated_at=now, valid_until=min(now+timedelta(minutes=5), latest.issued_at+timedelta(days=1), point.timestamp),
            validation_status='VALIDATED', transformation='NET_CARRY_USD_OVER_HORIZON',
            estimated_net_carry_usd=carry,
            scenario_loss_basis='FORECAST_NO_INCIDENT_VALUE',
            projected_collateral_value_usd=position.collateral_amount*point.q50,
            input_fingerprint=projection_fingerprint(snapshot, protocol.strategy_id, position_id),
            methodology='Conditional PT price change minus simple borrowing interest; fixed amounts, '
                        'loan USD price and user APR. No additional PT yield, no liquidation prediction. '
                        'Scenario losses must be incremental to this projected no-incident value.',
            backtest_reference='sha256:'+report_hash, evidence_ids=ids+(proof.evidence_id,)))
    return report, tuple(bundles), proof


def write_new(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as handle:
        handle.write(obj.model_dump_json(indent=2)+'\n' if hasattr(obj, 'model_dump_json')
                     else json.dumps(obj, indent=2)+'\n')


def load_raw_evidence(root):
    """Accept Data delivery raw/ev files and original ML collector directories."""
    archives = {}
    def add(identity, body):
        if identity in archives and archives[identity] != body:
            raise ValueError('conflicting raw bodies for one evidence ID')
        archives[identity] = body
    for path in root.rglob('ev_*.json'):
        add(path.stem, path.read_bytes())
    for path in root.rglob('evidence.json'):
        body = path.with_name('raw_ohlcv.json')
        if body.is_file():
            proof = EvidenceDTO.model_validate_json(path.read_text())
            add(proof.evidence_id, body.read_bytes())
    return archives


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    freeze = sub.add_parser('freeze')
    freeze.add_argument('--strategy-id', required=True)
    freeze.add_argument('--entity-id', required=True)
    freeze.add_argument('--revision', required=True)
    freeze.add_argument('--model', choices=['timesfm', 'chronos2', 'bolt'], default='timesfm')
    freeze.add_argument('--output', type=Path, required=True)
    for name in ('issue', 'evaluate', 'export'):
        p = sub.add_parser(name)
        p.add_argument('--protocol', type=Path, required=True)
        p.add_argument('--snapshot', type=Path, required=True)
        p.add_argument('--series', type=Path, required=True)
        p.add_argument('--output', type=Path, required=True)
        p.add_argument('--raw-evidence', type=Path, required=True,
                       help='directory of raw responses named <evidence_id>.json')
        if name != 'issue':
            p.add_argument('--forecasts', type=Path, required=True)
        if name == 'export':
            p.add_argument('--position-id', required=True)
            p.add_argument('--annual-borrow-apr', type=Decimal, required=True)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.command == 'freeze':
        write_new(args.output, Protocol(frozen_at=now, strategy_id=args.strategy_id,
                  entity_id=args.entity_id, revision=args.revision, model=args.model,
                  runtime_package_version=version('timesfm' if args.model == 'timesfm' else 'chronos-forecasting'),
                  configuration_sha256=configuration_hash(args.model)))
        return
    protocol = Protocol.model_validate_json(args.protocol.read_text())
    snapshot = load_snapshot(args.snapshot)
    series = TimeseriesDTO.model_validate_json(args.series.read_text())
    strategy = snapshot.strategy(protocol.strategy_id)
    raw_evidence = load_raw_evidence(args.raw_evidence)
    evidence = {e.evidence_id:e for e in snapshot.evidence}
    if args.command == 'issue':
        from .pretrained_candidates import load_candidate
        data = prepare_pt_series(series, strategy, source_snapshot_ref='sha256:'+digest(series),
                                 source_as_of=now, retrieved_at=now)
        evidence = {e.evidence_id: e for e in snapshot.evidence}
        if any(i not in evidence or evidence[i].status != 'OK'
               or evidence[i].data_mode.value != 'REAL' or evidence[i].fetched_at > now
               for i in data.evidence_ids):
            raise ValueError('issuing requires the REAL source proofs in the snapshot')
        model = load_candidate(protocol.model, revision=protocol.revision)
        forecast = issue(protocol, data, model, observations=series,
                         evidence=evidence, raw_evidence=raw_evidence)
        write_new(args.output, forecast)
        return
    forecasts = [IssuedForecast.model_validate_json(p.read_text()) for p in sorted(args.forecasts.glob('*.json'))]
    if args.command == 'evaluate':
        write_new(args.output, evaluate(protocol, forecasts, series, strategy, now,
                                        evidence=evidence, raw_evidence=raw_evidence))
    else:
        report, bundles, proof = project(protocol, forecasts, series, snapshot,
                                         args.position_id, args.annual_borrow_apr, now, raw_evidence=raw_evidence)
        # Export a new snapshot with proofs, preserving quotes and all other inputs.
        merged = tuple(f for f in snapshot.forecasts if not
                       (f.strategy_id == protocol.strategy_id and f.position_id == args.position_id)) + bundles
        updated = snapshot.model_copy(update={'snapshot_id': 'marilou:'+hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest(),
            'as_of': max(snapshot.as_of, now), 'forecasts': merged,
            'evidence': tuple(e for e in snapshot.evidence if e.evidence_id != proof.evidence_id)+(proof,)})
        if args.output.exists() or args.output.with_suffix('.validation.json').exists():
            raise FileExistsError('snapshot or validation report already exists; choose a new output')
        write_new(args.output, type(snapshot).model_validate(updated.model_dump()))
        write_new(args.output.with_suffix('.validation.json'), report)
        print(json.dumps({'published_horizons': [b.horizon_days for b in bundles],
                          'status': 'VALIDATED' if bundles else 'NOT_VALIDATED'}))


if __name__ == '__main__':
    main()
