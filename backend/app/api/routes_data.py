"""Read-only data API (prefix /api/v1). Handlers read storage only — never a provider.

Every success response is an `Envelope`. Its `status` is the stored DTO status, made
worse (STALE) when the data is older than its freshness TTL: stale or partial data is
never reported OK. DEMO data is only returned with data_mode=DEMO and always carries
DEMO_WARNING. Values are serialised with model_dump(mode="json"): Decimals are strings.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.ingestion.join import _worst_status
from app.ingestion.refresh import Freshness, freshness
from app.models.evidence import (
    SCHEMA_VERSION,
    DataMode,
    DataStatus,
    missing_fields,
    normalize_address,
    normalize_bytes32,
)
from app.models.market import MarketDTO, Protocol
from app.models.strategy import StrategyDTO, StrategyStatus
from app.models.timeseries import SeriesUnit, TimeseriesMetric
from app.storage.parquet_store import ObservationError, ParquetStore
from app.storage.sqlite_store import SqliteStore

router = APIRouter(prefix="/api/v1")

DEMO_WARNING = "DEMO data: simulated, not real"
_STRATEGY_ID_RE = re.compile(r"^strat_[0-9a-f]{16,64}$")
_EVIDENCE_ID_RE = re.compile(r"^ev_[0-9a-f]{16,64}$")
_POSITION_ID_RE = re.compile(r"^pos_[0-9a-z_\-]{4,64}$")
_STRATEGY_OPTIONAL = ("pendle_market_address", "underlying_asset", "expiry", "block_number")


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------

class Snapshot(BaseModel):
    data_mode: DataMode
    block_number: int | None = None
    block_hash: str | None = None


class Provenance(BaseModel):
    source_ids: list[str] = []
    evidence_ids: list[str] = []


class Envelope(BaseModel):
    schema_version: str = SCHEMA_VERSION
    status: DataStatus
    as_of: datetime | None
    snapshot: Snapshot
    data: Any
    provenance: Provenance
    missing_fields: list[str] = []
    warnings: list[str] = []


def _dedupe(values) -> list[str]:
    return list(dict.fromkeys(values))


def _served_status(status: DataStatus, fresh: Freshness | None, warnings: list[str]) -> DataStatus:
    """Stale data is at least STALE (worse statuses such as MISMATCH are kept)."""
    if fresh is not None and fresh.is_stale:
        age = int(fresh.age_seconds) if fresh.age_seconds is not None else None
        warnings.append(f"stale: age_seconds={age} ttl_seconds={fresh.ttl_seconds}")
        return _worst_status(status, DataStatus.STALE)
    return status


def _envelope(
    *, status: DataStatus, as_of: datetime | None, data_mode: DataMode, data: Any,
    source_ids=(), evidence_ids=(), missing=(), warnings: list[str] | None = None,
    block_number: int | None = None, block_hash: str | None = None,
) -> Envelope:
    warnings = list(warnings or [])
    if data_mode == DataMode.DEMO:
        warnings.append(DEMO_WARNING)
    return Envelope(
        status=status, as_of=as_of, data=data,
        snapshot=Snapshot(data_mode=data_mode, block_number=block_number, block_hash=block_hash),
        provenance=Provenance(source_ids=_dedupe(source_ids), evidence_ids=_dedupe(evidence_ids)),
        missing_fields=list(missing), warnings=warnings,
    )


def _market_missing(m: MarketDTO) -> list[str]:
    if m.morpho is not None:
        return missing_fields(m.morpho, m.morpho.METRIC_FIELDS, "morpho.")
    return missing_fields(m.pendle, m.pendle.METRIC_FIELDS, "pendle.")


def _market_view(m: MarketDTO, now: datetime) -> tuple[DataStatus, list[str], list[str]]:
    warnings: list[str] = []
    status = _served_status(m.status, freshness(m.as_of, "current", now), warnings)
    return status, _market_missing(m), warnings


def _strategy_view(s: StrategyDTO, now: datetime) -> tuple[DataStatus, list[str], list[str]]:
    warnings: list[str] = []
    status = _served_status(s.data_status, freshness(s.as_of, "verification", now), warnings)
    if s.status != StrategyStatus.VERIFIED_ACTIVE:
        warnings.append(f"strategy status {s.status.value}: {s.status_reason}")
    return status, missing_fields(s, _STRATEGY_OPTIONAL), warnings


def _list_envelope(dtos: list, views: list[tuple[DataStatus, list[str], list[str]]],
                   data_mode: DataMode) -> Envelope:
    """Worst status and oldest as_of over the items."""
    warnings = [f"{_key(d)}: {w}" for d, (_, _, ws) in zip(dtos, views) for w in ws]
    return _envelope(
        status=_worst_status(*(v[0] for v in views)),
        as_of=min(d.as_of for d in dtos),
        data_mode=data_mode,
        data=[d.model_dump(mode="json") for d in dtos],
        source_ids=(x for d in dtos for x in d.source_ids),
        evidence_ids=(x for d in dtos for x in d.evidence_ids),
        missing=[f"{_key(d)}: {f}" for d, (_, miss, _) in zip(dtos, views) for f in miss],
        warnings=warnings,
    )


def _key(dto: Any) -> str:
    return dto.market_key if isinstance(dto, MarketDTO) else dto.strategy_id


# ---------------------------------------------------------------------------
# Dependencies and id parsing
# ---------------------------------------------------------------------------

def get_store(request: Request) -> Iterator[SqliteStore]:
    """One connection per request (sqlite3 connections are thread-bound)."""
    try:
        store = SqliteStore(request.app.state.db_path)
    except Exception as exc:  # sqlite3.Error, OSError: storage itself unusable
        raise HTTPException(503, f"storage unavailable: {type(exc).__name__}") from None
    try:
        yield store
    finally:
        store.close()


def get_parquet(request: Request) -> ParquetStore:
    return ParquetStore(request.app.state.timeseries_root)


def get_now(request: Request) -> datetime:
    return request.app.state.clock()


def bad_request(detail: str) -> HTTPException:
    return HTTPException(400, detail)


def _check_chain(chain_id: int) -> None:
    if chain_id != 1:
        raise bad_request(f"chain_id {chain_id} not supported (MVP: 1)")


def parse_market_id(opaque_id: str) -> str:
    """morpho:1:<bytes32> | pendle:1:<address> -> canonical lowercase key."""
    parts = opaque_id.strip().split(":")
    if len(parts) != 3 or parts[0] not in ("morpho", "pendle") or not parts[1].isdigit():
        raise bad_request(f"malformed market id {opaque_id!r}: expected morpho:1:<bytes32> or pendle:1:<address>")
    _check_chain(int(parts[1]))
    try:
        ident = normalize_bytes32(parts[2]) if parts[0] == "morpho" else normalize_address(parts[2])
    except ValueError as exc:
        raise bad_request(f"malformed market id {opaque_id!r}: {exc}") from None
    return f"{parts[0]}:1:{ident}"


def parse_entity_id(entity_id: str) -> str:
    """Series entity: asset:1:<address> or a market id."""
    parts = entity_id.strip().split(":")
    if len(parts) == 3 and parts[0] == "asset" and parts[1].isdigit():
        _check_chain(int(parts[1]))
        try:
            return f"asset:1:{normalize_address(parts[2])}"
        except ValueError as exc:
            raise bad_request(f"malformed entity id {entity_id!r}: {exc}") from None
    return parse_market_id(entity_id)


def _match(pattern: re.Pattern, value: str, what: str) -> str:
    if not pattern.match(value):
        raise bad_request(f"malformed {what} {value!r}")
    return value


def _aware(dt: datetime | None, name: str) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        raise bad_request(f"{name} must include a timezone (e.g. 2026-10-01T00:00:00Z)")
    return dt.astimezone(timezone.utc) if dt else None


Mode = Query(DataMode.REAL, description="REAL (default) or DEMO; never mixed")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health", response_model=Envelope)
def health(store: SqliteStore = Depends(get_store), data_mode: DataMode = Mode) -> Envelope:
    latest = store.latest_as_of(data_mode)
    stamps = [t for t in latest.values() if t is not None]
    return _envelope(
        status=DataStatus.OK if stamps else DataStatus.MISSING,
        as_of=max(stamps) if stamps else None,
        data_mode=data_mode,
        data={"db": "reachable", "counts": store.counts(),
              "latest_as_of": {k: v.isoformat() if v else None for k, v in latest.items()}},
        warnings=[] if stamps else [f"no {data_mode.value} data stored"],
    )


@router.get("/markets", response_model=Envelope)
def list_markets(
    protocol: Protocol | None = None, chain_id: int = 1, data_mode: DataMode = Mode,
    store: SqliteStore = Depends(get_store), now: datetime = Depends(get_now),
) -> Envelope:
    _check_chain(chain_id)
    if not store.list_markets(chain_id=chain_id, data_mode=data_mode):
        raise HTTPException(503, f"no {data_mode.value} market data stored yet")
    markets = store.list_markets(protocol, chain_id, data_mode)
    if not markets:
        return _envelope(status=DataStatus.MISSING, as_of=None, data_mode=data_mode, data=[],
                         warnings=[f"no market for protocol={protocol.value if protocol else None}"])
    return _list_envelope(markets, [_market_view(m, now) for m in markets], data_mode)


@router.get("/markets/{opaque_id}", response_model=Envelope)
def get_market(
    opaque_id: str, data_mode: DataMode = Mode,
    store: SqliteStore = Depends(get_store), now: datetime = Depends(get_now),
) -> Envelope:
    key = parse_market_id(opaque_id)
    m = store.get_market(key, data_mode)
    if m is None:
        raise HTTPException(404, f"market {key} not found ({data_mode.value})")
    status, missing, warnings = _market_view(m, now)
    return _envelope(status=status, as_of=m.as_of, data_mode=data_mode, data=m.model_dump(mode="json"),
                     source_ids=m.source_ids, evidence_ids=m.evidence_ids, missing=missing,
                     warnings=warnings, block_number=m.block_number)


@router.get("/strategies", response_model=Envelope)
def list_strategies(
    status: StrategyStatus | None = None, data_mode: DataMode = Mode,
    store: SqliteStore = Depends(get_store), now: datetime = Depends(get_now),
) -> Envelope:
    if not store.list_strategies(data_mode=data_mode):
        raise HTTPException(503, f"no {data_mode.value} strategy data stored yet")
    strategies = store.list_strategies(status, data_mode)
    if not strategies:
        return _envelope(status=DataStatus.MISSING, as_of=None, data_mode=data_mode, data=[],
                         warnings=[f"no strategy with status={status.value if status else None}"])
    return _list_envelope(strategies, [_strategy_view(s, now) for s in strategies], data_mode)


@router.get("/strategies/{strategy_id}", response_model=Envelope)
def get_strategy(
    strategy_id: str, data_mode: DataMode = Mode,
    store: SqliteStore = Depends(get_store), now: datetime = Depends(get_now),
) -> Envelope:
    _match(_STRATEGY_ID_RE, strategy_id, "strategy id")
    s = store.get_strategy(strategy_id, data_mode)
    if s is None:
        raise HTTPException(404, f"strategy {strategy_id} not found ({data_mode.value})")
    nodes = [f"morpho:1:{s.morpho_market_id}"]
    if s.pendle_market_address:
        nodes.append(f"pendle:1:{s.pendle_market_address}")
    relations = {r.relation_id: r for n in nodes for r in store.list_relations(n, data_mode=data_mode)}
    status, missing, warnings = _strategy_view(s, now)
    return _envelope(
        status=status, as_of=s.as_of, data_mode=data_mode,
        data={"strategy": s.model_dump(mode="json"),
              "relations": [r.model_dump(mode="json") for r in relations.values()]},
        source_ids=s.source_ids,
        evidence_ids=[*s.evidence_ids, *(e for r in relations.values() for e in r.evidence_ids)],
        missing=missing, warnings=warnings, block_number=s.block_number,
    )


@router.get("/series", response_model=Envelope)
def get_series(
    entity_id: str, metric: TimeseriesMetric, source_id: str | None = None,
    start: datetime | None = None, end: datetime | None = None, data_mode: DataMode = Mode,
    store: SqliteStore = Depends(get_store), parquet: ParquetStore = Depends(get_parquet),
    now: datetime = Depends(get_now),
) -> Envelope:
    entity = parse_entity_id(entity_id)
    start, end = _aware(start, "start"), _aware(end, "end")
    try:
        rows = parquet.read_observations(entity, metric.value, data_mode, start, end, source_id)
        if not rows:
            raise HTTPException(404, f"no {metric.value} series for {entity} ({data_mode.value})")
        quote = None
        if rows[0].unit == SeriesUnit.TOKEN.value:
            # TOKEN series are quoted in the Morpho market's loan token; nothing else is stored.
            market = store.get_market(entity, data_mode) if entity.startswith("morpho:") else None
            if market is None:
                raise HTTPException(503, f"quote asset of {entity} {metric.value} unknown (market not stored)")
            quote = market.morpho.loan_token
        series = parquet.read_series(entity, metric.value, data_mode, start, end,
                                     source_id=source_id, quote_asset=quote)
    except ObservationError as exc:
        raise bad_request(str(exc)) from None
    warnings: list[str] = []
    last = series.points[-1]
    status = _served_status(series.status, freshness(last.timestamp, "history", now), warnings)
    n_missing = sum(1 for p in series.points if p.value is None)
    if n_missing:
        warnings.append(f"{n_missing} of {len(series.points)} points MISSING (not filled)")
    return _envelope(status=status, as_of=last.timestamp, data_mode=data_mode,
                     data=series.model_dump(mode="json"), source_ids=[series.source_id],
                     warnings=warnings, block_number=last.block_number)


@router.get("/positions/{position_id}", response_model=Envelope)
def get_position(
    position_id: str, data_mode: DataMode = Mode,
    store: SqliteStore = Depends(get_store), now: datetime = Depends(get_now),
) -> Envelope:
    _match(_POSITION_ID_RE, position_id, "position id")
    p = store.get_position(position_id, data_mode)
    if p is None:
        raise HTTPException(404, f"position {position_id} not found ({data_mode.value})")
    warnings: list[str] = []
    status = _served_status(p.status, freshness(p.as_of, "current", now), warnings)
    return _envelope(status=status, as_of=p.as_of, data_mode=data_mode, data=p.model_dump(mode="json"),
                     source_ids=p.source_ids, evidence_ids=p.evidence_ids,
                     missing=missing_fields(p, p.CRITICAL_FIELDS), warnings=warnings,
                     block_number=p.block_number)


@router.get("/data-status", response_model=Envelope)
def data_status(
    entity_id: str, data_mode: DataMode = Mode,
    store: SqliteStore = Depends(get_store), now: datetime = Depends(get_now),
) -> Envelope:
    if entity_id.startswith("strat_"):
        dto = store.get_strategy(_match(_STRATEGY_ID_RE, entity_id, "strategy id"), data_mode)
        kind, ttl_kind = "strategy", "verification"
    else:
        dto = store.get_market(parse_market_id(entity_id), data_mode)
        kind, ttl_kind = "market", "current"
    if dto is None:
        raise HTTPException(404, f"{kind} {entity_id} not found ({data_mode.value})")
    status, missing, warnings = _strategy_view(dto, now) if kind == "strategy" else _market_view(dto, now)
    fresh = freshness(dto.as_of, ttl_kind, now)
    return _envelope(
        status=status, as_of=dto.as_of, data_mode=data_mode,
        data={"kind": kind, "status": status.value,
              "freshness": {"as_of": dto.as_of.isoformat(), "age_seconds": int(fresh.age_seconds),
                            "ttl_seconds": fresh.ttl_seconds, "is_stale": fresh.is_stale},
              "missing_fields": missing, "evidence_count": len(dto.evidence_ids)},
        source_ids=dto.source_ids, evidence_ids=dto.evidence_ids, missing=missing, warnings=warnings,
        block_number=dto.block_number,
    )


@router.get("/evidence/{evidence_id}", response_model=Envelope)
def get_evidence(
    evidence_id: str, data_mode: DataMode = Mode, store: SqliteStore = Depends(get_store),
) -> Envelope:
    """Evidence is an immutable record of one past read: no freshness applies."""
    _match(_EVIDENCE_ID_RE, evidence_id, "evidence id")
    e = store.get_evidence(evidence_id, data_mode)
    if e is None:
        raise HTTPException(404, f"evidence {evidence_id} not found ({data_mode.value})")
    return _envelope(
        status=e.status, as_of=e.fetched_at, data_mode=data_mode, data=e.model_dump(mode="json"),
        source_ids=[e.source_id], evidence_ids=[e.evidence_id],
        warnings=[f"error: {e.error_message}"] if e.error_message else [],
        block_number=e.block_number, block_hash=e.block_hash,
    )
