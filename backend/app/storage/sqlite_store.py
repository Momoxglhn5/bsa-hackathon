"""SQLite store for contract DTOs (stdlib sqlite3, no ORM).

Each row = stable id + data_mode (primary key together, so REAL and DEMO never collide)
+ a few indexed lookup columns + `payload` = model.model_dump_json(). Decimals are
serialised as strings by the models, never floats. Reads re-validate the payload: a row
that does not validate raises, it is never returned partially or with defaults.

Timestamps are stored as fixed-width ISO UTC text so that string order == time order.
No business or risk logic here.
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, TypeVar

from pydantic import BaseModel

from app.models.evidence import DataMode, EvidenceDTO, RelationDTO, RelationType
from app.models.market import MarketDTO, Protocol
from app.models.position import PositionDTO
from app.models.strategy import StrategyDTO, StrategyStatus

DATA_DIR_ENV = "DOMINO_DATA_DIR"  # same variable as app.ingestion.collect
DB_FILENAME = "domino.sqlite"
SCHEMA_VERSION = 1
_BACKEND_DIR = Path(__file__).resolve().parents[2]

M = TypeVar("M", bound=BaseModel)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id  TEXT NOT NULL,
    data_mode    TEXT NOT NULL,
    chain_id     INTEGER NOT NULL,
    source_kind  TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    status       TEXT NOT NULL,
    block_number INTEGER,
    fetched_at   TEXT NOT NULL,
    payload      TEXT NOT NULL,
    PRIMARY KEY (evidence_id, data_mode)
);
CREATE INDEX IF NOT EXISTS ix_evidence_lookup ON evidence (data_mode, chain_id, source_kind, fetched_at);

CREATE TABLE IF NOT EXISTS markets (
    market_key TEXT NOT NULL,
    data_mode  TEXT NOT NULL,
    chain_id   INTEGER NOT NULL,
    protocol   TEXT NOT NULL,
    status     TEXT NOT NULL,
    as_of      TEXT NOT NULL,
    payload    TEXT NOT NULL,
    PRIMARY KEY (market_key, data_mode)
);
CREATE INDEX IF NOT EXISTS ix_markets_lookup ON markets (data_mode, chain_id, protocol, as_of);

CREATE TABLE IF NOT EXISTS strategies (
    strategy_id TEXT NOT NULL,
    data_mode   TEXT NOT NULL,
    chain_id    INTEGER NOT NULL,
    status      TEXT NOT NULL,
    data_status TEXT NOT NULL,
    as_of       TEXT NOT NULL,
    payload     TEXT NOT NULL,
    PRIMARY KEY (strategy_id, data_mode)
);
CREATE INDEX IF NOT EXISTS ix_strategies_lookup ON strategies (data_mode, chain_id, status, as_of);

CREATE TABLE IF NOT EXISTS relations (
    relation_id         TEXT NOT NULL,
    data_mode           TEXT NOT NULL,
    chain_id            INTEGER NOT NULL,
    source_node_id      TEXT NOT NULL,
    target_node_id      TEXT NOT NULL,
    relation_type       TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    observed_at         TEXT NOT NULL,
    payload             TEXT NOT NULL,
    PRIMARY KEY (relation_id, data_mode)
);
CREATE INDEX IF NOT EXISTS ix_relations_source ON relations (data_mode, source_node_id);
CREATE INDEX IF NOT EXISTS ix_relations_target ON relations (data_mode, target_node_id);
CREATE INDEX IF NOT EXISTS ix_relations_type ON relations (data_mode, relation_type);

CREATE TABLE IF NOT EXISTS positions (
    position_id TEXT NOT NULL,
    data_mode   TEXT NOT NULL,
    chain_id    INTEGER NOT NULL,
    status      TEXT NOT NULL,
    strategy_id TEXT,
    as_of       TEXT NOT NULL,
    payload     TEXT NOT NULL,
    PRIMARY KEY (position_id, data_mode)
);
CREATE INDEX IF NOT EXISTS ix_positions_lookup ON positions (data_mode, chain_id, strategy_id);

CREATE TABLE IF NOT EXISTS raw_snapshots (
    snapshot_id  TEXT NOT NULL,
    data_mode    TEXT NOT NULL,
    chain_id     INTEGER NOT NULL,
    path         TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    complete     INTEGER NOT NULL,
    record_count INTEGER NOT NULL,
    error_count  INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, data_mode)
);
CREATE INDEX IF NOT EXISTS ix_raw_snapshots_started ON raw_snapshots (data_mode, started_at);
"""


def default_data_dir() -> Path:
    """<DOMINO_DATA_DIR> if set, else backend/data (git-ignored)."""
    data_dir = os.environ.get(DATA_DIR_ENV, "").strip()
    return Path(data_dir) if data_dir else _BACKEND_DIR / "data"


def default_db_path() -> Path:
    return default_data_dir() / DB_FILENAME


def _ts(dt: datetime | None) -> str | None:
    """Fixed-width ISO UTC (microseconds always present): lexical order == time order."""
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


@dataclass(frozen=True)
class RawSnapshotMeta:
    snapshot_id: str
    data_mode: DataMode
    chain_id: int
    path: str
    started_at: datetime
    finished_at: datetime | None
    complete: bool
    record_count: int
    error_count: int


class SqliteStore:
    def __init__(self, path: str | Path | None = None) -> None:
        path = default_db_path() if path is None else path
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        with self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- helpers -------------------------------------------------------------

    def _write(self, sql: str, rows: Iterable[tuple]) -> int:
        """Run in one transaction; returns rows actually inserted/updated."""
        with self._conn:
            before = self._conn.total_changes
            self._conn.executemany(sql, list(rows))
            return self._conn.total_changes - before

    def _one(self, model: type[M], sql: str, params: tuple) -> M | None:
        row = self._conn.execute(sql, params).fetchone()
        return model.model_validate_json(row[0]) if row else None

    def _many(self, model: type[M], sql: str, params: tuple) -> list[M]:
        return [model.model_validate_json(r[0]) for r in self._conn.execute(sql, params)]

    @staticmethod
    def _upsert_newer(table: str, key: str, cols: list[str], ts_col: str) -> str:
        """Insert, or replace only when the incoming timestamp is newer or equal."""
        all_cols = [key, "data_mode", *cols, "payload"]
        updates = ", ".join(f"{c} = excluded.{c}" for c in [*cols, "payload"])
        return (
            f"INSERT INTO {table} ({', '.join(all_cols)}) VALUES ({', '.join('?' * len(all_cols))}) "
            f"ON CONFLICT ({key}, data_mode) DO UPDATE SET {updates} "
            f"WHERE excluded.{ts_col} >= {table}.{ts_col}"
        )

    # -- writes --------------------------------------------------------------

    def save_evidence(self, items: Iterable[EvidenceDTO]) -> int:
        """Immutable: an existing evidence_id keeps its original row."""
        return self._write(
            "INSERT OR IGNORE INTO evidence (evidence_id, data_mode, chain_id, source_kind, source_id, "
            "status, block_number, fetched_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ((e.evidence_id, e.data_mode.value, e.chain_id, e.source_kind.value, e.source_id,
              e.status.value, e.block_number, _ts(e.fetched_at), e.model_dump_json()) for e in items),
        )

    def save_markets(self, items: Iterable[MarketDTO]) -> int:
        return self._write(
            self._upsert_newer("markets", "market_key", ["chain_id", "protocol", "status", "as_of"], "as_of"),
            ((m.market_key, m.data_mode.value, m.chain_id, m.protocol.value, m.status.value,
              _ts(m.as_of), m.model_dump_json()) for m in items),
        )

    def save_strategies(self, items: Iterable[StrategyDTO]) -> int:
        return self._write(
            self._upsert_newer(
                "strategies", "strategy_id", ["chain_id", "status", "data_status", "as_of"], "as_of"
            ),
            ((s.strategy_id, s.data_mode.value, s.chain_id, s.status.value, s.data_status.value,
              _ts(s.as_of), s.model_dump_json()) for s in items),
        )

    def save_relations(self, items: Iterable[RelationDTO]) -> int:
        return self._write(
            self._upsert_newer(
                "relations", "relation_id",
                ["chain_id", "source_node_id", "target_node_id", "relation_type",
                 "verification_status", "observed_at"],
                "observed_at",
            ),
            ((r.relation_id, r.data_mode.value, r.chain_id, r.source_node_id, r.target_node_id,
              r.relation_type.value, r.verification_status.value, _ts(r.observed_at),
              r.model_dump_json()) for r in items),
        )

    def save_positions(self, items: Iterable[PositionDTO]) -> int:
        return self._write(
            "INSERT INTO positions (position_id, data_mode, chain_id, status, strategy_id, as_of, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (position_id, data_mode) DO UPDATE SET "
            "chain_id = excluded.chain_id, status = excluded.status, strategy_id = excluded.strategy_id, "
            "as_of = excluded.as_of, payload = excluded.payload",
            ((p.position_id, p.data_mode.value, p.chain_id, p.status.value, p.strategy_id,
              _ts(p.as_of), p.model_dump_json()) for p in items),
        )

    def save_raw_snapshot_meta(self, snapshot_dir: str | Path) -> int:
        """Index a raw snapshot directory written by app.ingestion.collect (its manifest.json)."""
        snapshot_dir = Path(snapshot_dir)
        m = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
        data_mode = DataMode(m["data_mode"])
        finished = m.get("finished_at")
        return self._write(
            "INSERT OR IGNORE INTO raw_snapshots (snapshot_id, data_mode, chain_id, path, started_at, "
            "finished_at, complete, record_count, error_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(m["snapshot_id"], data_mode.value, int(m["chain_id"]), str(snapshot_dir.resolve()),
              _ts(_parse_ts(m["started_at"])), _ts(_parse_ts(finished)) if finished else None,
              int(bool(m["complete"])), len(m["records"]), len(m["errors"]))],
        )

    # -- reads ---------------------------------------------------------------

    def get_evidence(self, evidence_id: str, data_mode: DataMode = DataMode.REAL) -> EvidenceDTO | None:
        return self._one(EvidenceDTO, "SELECT payload FROM evidence WHERE evidence_id = ? AND data_mode = ?",
                         (evidence_id, data_mode.value))

    def get_market(self, market_key: str, data_mode: DataMode = DataMode.REAL) -> MarketDTO | None:
        return self._one(MarketDTO, "SELECT payload FROM markets WHERE market_key = ? AND data_mode = ?",
                         (market_key, data_mode.value))

    def list_markets(
        self, protocol: Protocol | None = None, chain_id: int = 1, data_mode: DataMode = DataMode.REAL
    ) -> list[MarketDTO]:
        sql = "SELECT payload FROM markets WHERE data_mode = ? AND chain_id = ?"
        params: tuple = (data_mode.value, chain_id)
        if protocol is not None:
            sql, params = sql + " AND protocol = ?", params + (protocol.value,)
        return self._many(MarketDTO, sql + " ORDER BY market_key", params)

    def get_strategy(self, strategy_id: str, data_mode: DataMode = DataMode.REAL) -> StrategyDTO | None:
        return self._one(StrategyDTO, "SELECT payload FROM strategies WHERE strategy_id = ? AND data_mode = ?",
                         (strategy_id, data_mode.value))

    def list_strategies(
        self, status: StrategyStatus | None = None, data_mode: DataMode = DataMode.REAL
    ) -> list[StrategyDTO]:
        sql = "SELECT payload FROM strategies WHERE data_mode = ?"
        params: tuple = (data_mode.value,)
        if status is not None:
            sql, params = sql + " AND status = ?", params + (status.value,)
        return self._many(StrategyDTO, sql + " ORDER BY strategy_id", params)

    def list_relations(
        self,
        node_id: str | None = None,
        relation_type: RelationType | None = None,
        data_mode: DataMode = DataMode.REAL,
    ) -> list[RelationDTO]:
        """All relations touching `node_id` (as source or target)."""
        sql = "SELECT payload FROM relations WHERE data_mode = ?"
        params: tuple = (data_mode.value,)
        if node_id is not None:
            sql, params = sql + " AND (source_node_id = ? OR target_node_id = ?)", params + (node_id, node_id)
        if relation_type is not None:
            sql, params = sql + " AND relation_type = ?", params + (relation_type.value,)
        return self._many(RelationDTO, sql + " ORDER BY relation_id", params)

    def get_position(self, position_id: str, data_mode: DataMode = DataMode.REAL) -> PositionDTO | None:
        return self._one(PositionDTO, "SELECT payload FROM positions WHERE position_id = ? AND data_mode = ?",
                         (position_id, data_mode.value))

    def list_positions(self, data_mode: DataMode = DataMode.REAL) -> list[PositionDTO]:
        return self._many(PositionDTO, "SELECT payload FROM positions WHERE data_mode = ? ORDER BY position_id",
                          (data_mode.value,))

    def latest_raw_snapshot(self, data_mode: DataMode = DataMode.REAL) -> RawSnapshotMeta | None:
        row = self._conn.execute(
            "SELECT snapshot_id, data_mode, chain_id, path, started_at, finished_at, complete, "
            "record_count, error_count FROM raw_snapshots WHERE data_mode = ? "
            "ORDER BY started_at DESC LIMIT 1",
            (data_mode.value,),
        ).fetchone()
        if row is None:
            return None
        return RawSnapshotMeta(
            snapshot_id=row[0], data_mode=DataMode(row[1]), chain_id=row[2], path=row[3],
            started_at=_parse_ts(row[4]), finished_at=_parse_ts(row[5]) if row[5] else None,
            complete=bool(row[6]), record_count=row[7], error_count=row[8],
        )

    def latest_as_of(self, data_mode: DataMode = DataMode.REAL) -> dict[str, datetime | None]:
        """Most recent timestamp per table for one data mode (None when empty)."""
        columns = {
            "evidence": "fetched_at", "markets": "as_of", "strategies": "as_of",
            "relations": "observed_at", "positions": "as_of", "raw_snapshots": "started_at",
        }
        out: dict[str, datetime | None] = {}
        for table, col in columns.items():
            row = self._conn.execute(f"SELECT MAX({col}) FROM {table} WHERE data_mode = ?",
                                     (data_mode.value,)).fetchone()
            out[table] = _parse_ts(row[0]) if row[0] else None
        return out

    def counts(self) -> dict[str, int]:
        tables = ("evidence", "markets", "strategies", "relations", "positions", "raw_snapshots")
        return {t: self._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
