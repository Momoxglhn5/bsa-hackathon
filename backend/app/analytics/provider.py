"""Read the existing Data stores; engines never call external providers.

A content hash pins simulations to one immutable dataset. A refresh creates a new
service, so an older simulation cannot silently enter a newer decision.
"""
import hashlib
import json
import sqlite3
from collections import OrderedDict
from datetime import timedelta
from threading import RLock
from pathlib import Path

from fastapi import HTTPException

from app.analytics.dataset import AnalyticsSnapshot, AnalyticsInputs
from app.analytics.service import AnalyticsService
from app.models.evidence import DataMode, DataStatus
from app.models.market import PricingMethod
from app.models.timeseries import SeriesUnit, SubjectKind, TimeseriesDTO, TimeseriesMetric, TimeseriesPointDTO
from app.stress.monte_carlo.calibration import ORACLE_PRICE_METHOD, RPC_SOURCE_ID, is_sampling_candidate
from app.storage.parquet_store import ParquetStore, ObservationError
from app.storage.sqlite_store import SqliteStore

# Calibration needs 30 days; a little margin keeps late midnight samples.
_ORACLE_HISTORY_DAYS = 40
_FLAG_STATUSES = {s.value for s in DataStatus} - {DataStatus.OK.value}


def _oracle_series(store, parquet, market, data_mode):
    """RPC oracle history of one Morpho market, reduced to calibration sampling candidates.

    Parquet rows carry no evidence ids; each point is linked to the RPC reads of the
    market oracle at its block. The calibration re-checks every proof.
    """
    try:
        rows = parquet.read_observations(market.market_key, TimeseriesMetric.ORACLE_PRICE.value,
                                         data_mode, source_id=RPC_SOURCE_ID)
    except ObservationError:
        return None, ()  # unusable history stays absent; Monte Carlo reports it missing
    rows = [r for r in rows if is_sampling_candidate(r.ts_utc)]
    if rows:
        rows = [r for r in rows if r.ts_utc >= rows[-1].ts_utc - timedelta(days=_ORACLE_HISTORY_DAYS)]
    if not rows:
        return None, ()
    oracle = market.morpho.oracle_address
    proofs = {}
    for e in store.find_rpc_evidence(oracle, {r.block_number for r in rows if r.block_number is not None}, data_mode):
        if e.method == ORACLE_PRICE_METHOD:
            proofs.setdefault(e.block_number, []).append(e)
    points = [TimeseriesPointDTO(
        timestamp=r.ts_utc, value=r.value_decimal,
        status=next((DataStatus(f) for f in r.quality_flags if f in _FLAG_STATUSES), DataStatus.OK),
        block_number=r.block_number,
        evidence_ids=tuple(e.evidence_id for e in proofs.get(r.block_number, ())),
    ) for r in rows]
    statuses = {p.status for p in points}
    try:
        dto = TimeseriesDTO(
            chain_id=market.chain_id, data_mode=data_mode,
            series_id="ts_oracle_price_" + hashlib.sha256(market.market_key.encode()).hexdigest()[:24],
            metric=TimeseriesMetric.ORACLE_PRICE, subject_kind=SubjectKind.MARKET, subject_id=market.market_id,
            unit=SeriesUnit(rows[0].unit), quote_asset=market.morpho.loan_token,
            pricing_method=PricingMethod.MORPHO_ORACLE, source_id=RPC_SOURCE_ID,
            status=DataStatus.OK if statuses == {DataStatus.OK} else
            DataStatus.PARTIAL if any(p.value is not None for p in points) else DataStatus.MISSING,
            points=tuple(points),
        )
    except ValueError:
        return None, ()
    return dto, tuple(e for es in proofs.values() for e in es)


def snapshot_from_store(store, parquet, data_mode=DataMode.REAL, *, inputs=None):
    inputs = inputs or AnalyticsInputs()
    markets = store.list_markets(data_mode=data_mode)
    # Metadata refreshes store every candidate, but only analysed markets are stored:
    # keep strategies whose Morpho market is present (joined by chain and market id).
    morpho_keys = {m.market_key for m in markets if m.morpho is not None}
    strategies = [s for s in store.list_strategies(data_mode=data_mode)
                  if f"morpho:{s.chain_id}:{s.morpho_market_id}" in morpho_keys]
    if not markets or not strategies:
        raise HTTPException(503, f"No {data_mode.value} analytics dataset stored; run the Data pipeline or select an explicit offline snapshot")
    positions = store.list_positions(data_mode)
    relations = store.list_relations(data_mode=data_mode)
    series = []
    for market in markets:
        # Risk history currently consumes only USD lending liquidity history.
        metric = TimeseriesMetric.LIQUIDITY_USD.value
        rows = parquet.read_observations(market.market_key, metric, data_mode)
        for source in sorted({r.source_id for r in rows}):
            dto = parquet.read_series(market.market_key, metric, data_mode, source_id=source)
            if dto:
                series.append(dto)
    oracle_evidence = []
    for market in markets:
        if market.morpho is not None:
            dto, proofs = _oracle_series(store, parquet, market, data_mode)
            if dto is not None:
                series.append(dto)
                oracle_evidence += proofs
    ids = {i for x in (*markets, *strategies, *positions, *relations) for i in x.evidence_ids}
    ids.update(i for m in markets for c in m.verification for i in c.evidence_ids)
    ids.update(i for s in series for p in s.points for i in p.evidence_ids)
    ids.update(i for m in markets for fields in (m.morpho, m.pendle) if fields
               for name in ("collateral_price", "loan_price", "oracle_price", "pt_price")
               if (price := getattr(fields, name, None)) for i in price.evidence_ids)
    ids.update(i for q in inputs.quotes for i in (*q.evidence_ids, *q.quote.evidence_ids,
        *(q.output_usd_price.evidence_ids if q.output_usd_price else ())))
    ids.update(i for observation in (*inputs.risk_observations, *inputs.forecasts) for i in observation.evidence_ids)
    ids -= {e.evidence_id for e in oracle_evidence}
    evidence = [e for i in sorted(ids) if (e := store.get_evidence(i, data_mode)) is not None]
    evidence = sorted([*evidence, *{e.evidence_id: e for e in oracle_evidence}.values()],
                      key=lambda e: e.evidence_id)
    timestamps = [x.as_of for x in (*markets, *strategies, *positions)]
    timestamps += [r.observed_at for r in relations] + [e.fetched_at for e in evidence]
    snapshot = AnalyticsSnapshot(snapshot_id="pending", as_of=max(timestamps),
        description=f"Immutable export from Data SQLite/Parquet ({data_mode.value}); missing prices and quotes remain unknown.",
        markets=tuple(markets), strategies=tuple(strategies), positions=tuple(positions),
        relations=tuple(relations), evidence=tuple(evidence), series=tuple(series),
        quotes=inputs.quotes, risk_observations=inputs.risk_observations, forecasts=inputs.forecasts)
    digest = hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest()[:24]
    return snapshot.model_copy(update={"snapshot_id": f"stored:{data_mode.value}:{digest}"})


class StoreAnalyticsProvider:
    def __init__(self, db_path, timeseries_root, clock, *, replay=False, inputs_path=None):
        self.db_path, self.timeseries_root, self.clock = db_path, timeseries_root, clock
        self.replay = replay
        self.inputs_path = inputs_path
        self._services = OrderedDict()
        self._lock = RLock()

    def get(self, data_mode):
        try:
            inputs_path = Path(self.inputs_path) if self.inputs_path else None
            if inputs_path is None and data_mode == DataMode.REAL:
                delivery = Path(self.db_path).parent / "delivery"
                pointer = delivery / "current.json"
                if pointer.exists():
                    bundle = json.loads(pointer.read_text())["bundle"]
                    if not isinstance(bundle, str) or Path(bundle).name != bundle or bundle in (".", ".."):
                        raise ValueError("Invalid Data delivery pointer")
                    inputs_path = delivery / bundle / "analytics_inputs.json"
            inputs = AnalyticsInputs.model_validate_json(inputs_path.read_text()) if inputs_path else AnalyticsInputs()
            with SqliteStore(self.db_path) as store:
                # Keep the multi-table read coherent while refresh writes in parallel.
                store._conn.execute("BEGIN")
                snapshot = snapshot_from_store(store, ParquetStore(self.timeseries_root), data_mode, inputs=inputs)
        except (sqlite3.Error, OSError, ObservationError, ValueError, KeyError) as exc:
            raise HTTPException(503, f"Analytics storage unavailable: {type(exc).__name__}") from None
        with self._lock:
            key = snapshot.snapshot_id
            if key not in self._services:
                self._services[key] = AnalyticsService(snapshot, replay=self.replay, clock=self.clock)
            self._services.move_to_end(key)
            while len(self._services) > 8:
                self._services.popitem(last=False)
            return self._services[key]
