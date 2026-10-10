"""Read the existing Data stores; engines never call external providers.

A content hash pins simulations to one immutable dataset. A refresh creates a new
service, so an older simulation cannot silently enter a newer decision.
"""
import hashlib
import sqlite3
from collections import OrderedDict
from threading import RLock
from pathlib import Path

from fastapi import HTTPException

from app.analytics.dataset import AnalyticsSnapshot, AnalyticsInputs
from app.analytics.service import AnalyticsService
from app.models.evidence import DataMode
from app.models.timeseries import TimeseriesMetric
from app.storage.parquet_store import ParquetStore, ObservationError
from app.storage.sqlite_store import SqliteStore


def snapshot_from_store(store, parquet, data_mode=DataMode.REAL, *, inputs=None):
    inputs = inputs or AnalyticsInputs()
    markets = store.list_markets(data_mode=data_mode)
    strategies = store.list_strategies(data_mode=data_mode)
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
    ids = {i for x in (*markets, *strategies, *positions, *relations) for i in x.evidence_ids}
    ids.update(i for m in markets for c in m.verification for i in c.evidence_ids)
    ids.update(i for m in markets for fields in (m.morpho, m.pendle) if fields
               for name in ("collateral_price", "loan_price", "oracle_price", "pt_price")
               if (price := getattr(fields, name, None)) for i in price.evidence_ids)
    ids.update(i for q in inputs.quotes for i in (*q.evidence_ids, *q.quote.evidence_ids,
        *(q.output_usd_price.evidence_ids if q.output_usd_price else ())))
    ids.update(i for observation in (*inputs.risk_observations, *inputs.forecasts) for i in observation.evidence_ids)
    evidence = [e for i in sorted(ids) if (e := store.get_evidence(i, data_mode)) is not None]
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
            inputs = AnalyticsInputs.model_validate_json(Path(self.inputs_path).read_text()) if self.inputs_path else AnalyticsInputs()
            with SqliteStore(self.db_path) as store:
                # Keep the multi-table read coherent while refresh writes in parallel.
                store._conn.execute("BEGIN")
                snapshot = snapshot_from_store(store, ParquetStore(self.timeseries_root), data_mode, inputs=inputs)
        except (sqlite3.Error, OSError, ObservationError) as exc:
            raise HTTPException(503, f"Analytics storage unavailable: {type(exc).__name__}") from None
        with self._lock:
            key = snapshot.snapshot_id
            if key not in self._services:
                self._services[key] = AnalyticsService(snapshot, replay=self.replay, clock=self.clock)
            self._services.move_to_end(key)
            while len(self._services) > 8:
                self._services.popitem(last=False)
            return self._services[key]
