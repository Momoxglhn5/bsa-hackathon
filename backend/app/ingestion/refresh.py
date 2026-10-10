"""Refresh scheduler for the demo strategy: current state, metadata, verification, history.

Cadences (= freshness TTLs, from the step-23 brief):
  * current market state / prices ... 300 s   (refresh_current)
  * market lists / candidates ....... 1800 s  (refresh_metadata)
  * on-chain strategy verification .. 86400 s (verify_onchain)
  * history backfill ................ 86400 s (backfill_history)

Rules:
  * partial failures are recorded in TaskResult.errors; nothing is zero-filled;
  * stored DTOs are never mutated to mark staleness: `get_with_freshness` returns the
    stored DTO together with a Freshness computed at read time (stale stays stale);
  * refresh_metadata never promotes and never overwrites a verification outcome;
  * market rows are written only by refresh_current (the DTO that carries RPC reads).

CLI (from backend/):
  python -m app.ingestion.refresh --once | --loop [--sleep 30] | --task <name>
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.adapters.ethereum_rpc import (
    EthereumRPCError,
    EthereumRpcClient,
    PinnedBlock,
    read_erc20_decimals,
    read_morpho_market_params,
    read_morpho_market_state,
    read_oracle_price,
    read_pendle_market_tokens,
)
from app.adapters.morpho_api import MorphoAPIError
from app.adapters.pendle_api import PendleAPIError
from app.ingestion.backfill import PENDLE_SOURCE, run_backfill
from app.ingestion.join import find_candidate_strategies
from app.ingestion.normalize import (
    NormalizationError,
    SourceRef,
    TokenMeta,
    morpho_apys_from_graphql,
    morpho_market_key,
    normalize_morpho_market,
    normalize_pendle_market,
    pendle_market_key,
)
from app.ingestion.verify import RawItems, find_listing_item, load_morpho, load_pendle, verify_and_promote
from app.models.evidence import DataMode, EvidenceDTO, normalize_address, normalize_bytes32
from app.models.market import MarketDTO, PricingMethod, TokenRef
from app.models.strategy import StrategyDTO, StrategyStatus, make_strategy_id
from app.models.timeseries import SeriesUnit, TimeseriesMetric
from app.storage.parquet_store import (
    FLAG_MISSING,
    FLAG_SNAPSHOT,
    Observation,
    ObservationError,
    ParquetStore,
)
from app.storage.sqlite_store import SqliteStore

logger = logging.getLogger(__name__)

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "demo_strategy_manifest.json"

FRESHNESS_TTL: dict[str, int] = {
    "current": 300,
    "markets_list": 1800,
    "verification": 86400,
    "history": 86400,
}
# Task -> run interval. apply_maturity is local and cheap: checked with every current refresh.
TASK_INTERVALS: dict[str, int] = {
    "refresh_metadata": FRESHNESS_TTL["markets_list"],
    "verify_onchain": FRESHNESS_TTL["verification"],
    "refresh_current": FRESHNESS_TTL["current"],
    "backfill_history": FRESHNESS_TTL["history"],
    "apply_maturity": FRESHNESS_TTL["current"],
}
# get_with_freshness kind -> (TTL kind)
_SERVED_KINDS = {"market": "current", "strategy": "verification"}
# Verification outcomes that a metadata refresh must not overwrite with a fresh CANDIDATE.
_VERIFIED_STATUSES = frozenset({StrategyStatus.VERIFIED_ACTIVE, StrategyStatus.UNSUPPORTED})


# ---------------------------------------------------------------------------
# Freshness (pure)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Freshness:
    as_of: datetime | None
    age_seconds: float | None
    ttl_seconds: int
    is_stale: bool


def freshness(as_of: datetime | None, kind: str, now: datetime) -> Freshness:
    """No data (as_of None) counts as stale."""
    ttl = FRESHNESS_TTL[kind]
    if as_of is None:
        return Freshness(None, None, ttl, True)
    age = (now - as_of).total_seconds()
    return Freshness(as_of, age, ttl, age > ttl)


def apply_maturity(strategy: StrategyDTO, now: datetime) -> StrategyDTO:
    """EXPIRED copy when the PT expiry is reached; otherwise the same object. No network."""
    if strategy.expiry is None or strategy.expiry > now or strategy.status in (
        StrategyStatus.EXPIRED, StrategyStatus.UNSUPPORTED
    ):
        return strategy
    return StrategyDTO.model_validate({
        **strategy.model_dump(),
        "status": StrategyStatus.EXPIRED,
        "status_reason": "PT expiry reached",
        "as_of": now,
    })


@dataclass
class TaskResult:
    name: str
    ok: bool
    started_at: datetime
    finished_at: datetime
    written: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Target:
    morpho_market_id: str
    pendle_market_address: str
    # Morpho collateral == PT: used as a server-side listing filter. None -> full scan.
    pt_address: str | None = None

    @classmethod
    def from_strategy(cls, s: StrategyDTO) -> "Target":
        if s.pendle_market_address is None:
            raise ValueError(f"{s.strategy_id}: no Pendle market selected")
        return cls(s.morpho_market_id, s.pendle_market_address, s.pt.address)

    @classmethod
    def from_manifest(cls, path: Path = MANIFEST_PATH) -> "Target":
        m = json.loads(path.read_text(encoding="utf-8"))
        pt = m.get("ptTokenAddress")
        return cls(normalize_bytes32(m["morphoMarketId"]), normalize_address(m["pendleMarketAddress"]),
                   normalize_address(pt) if pt else None)


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


def _snapshot(entity: str, metric: TimeseriesMetric, ts: datetime, value: Any, unit: SeriesUnit,
              source_id: str, retrieved_at: datetime, block: int | None = None,
              method: PricingMethod | None = None) -> Observation:
    """Current-state point; a None value is stored as MISSING, never 0."""
    return Observation(
        entity_id=entity, metric=metric.value, ts_utc=ts, value_decimal=value, unit=unit.value,
        source_id=source_id, retrieved_at=retrieved_at,
        quality_flags=(FLAG_SNAPSHOT,) if value is not None else (FLAG_SNAPSHOT, FLAG_MISSING),
        block_number=block, pricing_method=method.value if method else None,
    )


# ---------------------------------------------------------------------------
# Refresher
# ---------------------------------------------------------------------------

class Refresher:
    """Tasks over injected clients and stores (fakes in tests)."""

    def __init__(
        self,
        *,
        morpho: Any,
        pendle: Any,
        sqlite: SqliteStore,
        parquet: ParquetStore,
        rpc_factory: Callable[[], EthereumRpcClient] = EthereumRpcClient,
        target: Target | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.morpho, self.pendle = morpho, pendle
        self.sqlite, self.parquet = sqlite, parquet
        self._rpc_factory = rpc_factory
        self._rpc: EthereumRpcClient | None = None
        self._target = target
        self.clock = clock

    # -- helpers -------------------------------------------------------------

    def target(self, strategy: StrategyDTO | None = None) -> Target:
        if strategy is not None:
            return Target.from_strategy(strategy)
        if self._target is None:
            self._target = Target.from_manifest()
        return self._target

    def rpc(self) -> EthereumRpcClient:
        """Connect lazily; a failed connection is retried on the next call."""
        if self._rpc is None:
            self._rpc = self._rpc_factory()
        return self._rpc

    def _flush_rpc_evidence(self, written: dict[str, int]) -> None:
        if self._rpc is not None and self._rpc.evidence:
            written["evidence"] = written.get("evidence", 0) + self.sqlite.save_evidence(self._rpc.evidence.values())
            self._rpc.evidence.clear()

    def _save_api_evidence(self, raws: list[Any], written: dict[str, int]) -> None:
        evidence: list[EvidenceDTO] = [r.to_evidence() for r in raws]
        written["evidence"] = written.get("evidence", 0) + self.sqlite.save_evidence(evidence)

    def _run(self, name: str, fn: Callable[[dict[str, int], list[str]], None]) -> TaskResult:
        started = self.clock()
        written: dict[str, int] = {}
        errors: list[str] = []
        try:
            fn(written, errors)
        except Exception as exc:  # a task never crashes the scheduler; the error is reported
            logger.exception("task %s failed", name)
            errors.append(_err(exc))
        return TaskResult(name, not errors, started, self.clock(), written, errors)

    # -- tasks ---------------------------------------------------------------

    def refresh_current(self, strategy: StrategyDTO | None = None) -> TaskResult:
        """Morpho + Pendle current state (API items + RPC reads at one block) -> SQLite + Parquet."""
        return self._run("refresh_current", lambda w, e: self._refresh_current(self.target(strategy), w, e))

    def _refresh_current(self, t: Target, written: dict[str, int], errors: list[str]) -> None:
        now = self.clock()
        market_id, pendle_addr = t.morpho_market_id, t.pendle_market_address

        # 1. RPC reads at one pinned block (each optional; failures recorded).
        pinned: PinnedBlock | None = None
        reads: dict[str, Any] = {}
        try:
            rpc = self.rpc()
            pinned = rpc.pin_block()
        except EthereumRPCError as exc:
            errors.append(f"rpc: {_err(exc)}")
        if pinned is not None:
            readers = {
                "params": lambda: read_morpho_market_params(rpc, market_id, pinned),
                "state": lambda: read_morpho_market_state(rpc, market_id, pinned),
                "pendle_tokens": lambda: read_pendle_market_tokens(rpc, pendle_addr, pinned)[0],
            }
            for name, read in readers.items():
                try:
                    reads[name] = read()
                except EthereumRPCError as exc:
                    errors.append(f"rpc {name}: {_err(exc)}")
            if "params" in reads:
                p = reads["params"]
                try:
                    reads["oracle"] = read_oracle_price(rpc, p.oracle, pinned)
                except EthereumRPCError as exc:
                    errors.append(f"rpc oracle: {_err(exc)}")
                tokens: dict[str, TokenMeta] = {}
                for addr in (p.loan_token, p.collateral_token):
                    try:
                        res = read_erc20_decimals(rpc, addr, pinned)
                        tokens[addr] = TokenMeta(addr, int(res.value), None, res.source_ref)
                    except EthereumRPCError as exc:
                        errors.append(f"rpc decimals {addr}: {_err(exc)}")
                reads["tokens"] = tokens

        # 2. Morpho: API item (+ APYs) normalised with the RPC reads.
        api_raws: list[Any] = []
        markets: list[MarketDTO] = []
        morpho_dto = apys_raw = None
        try:
            listing = self.morpho.list_markets(collateral_token=t.pt_address)
            found = find_listing_item(listing, "market_id", market_id)
            if found is None:
                raise LookupError(f"market not in Morpho listing ({listing.stop_reason}; "
                                  f"{[str(x) for x in listing.errors]})")
            item, raw = found
            api_raws.append(raw)
            apys = None
            try:
                apys_raw = self.morpho.fetch_market_details(market_id)
                api_raws.append(apys_raw)
                apys = morpho_apys_from_graphql(apys_raw.payload, SourceRef.from_response(apys_raw))
            except MorphoAPIError as exc:
                errors.append(f"morpho apys: {_err(exc)}")
            morpho_dto = normalize_morpho_market(
                item, SourceRef.from_response(raw),
                onchain_params=reads.get("params"), onchain_state=reads.get("state"),
                oracle=reads.get("oracle"), apys=apys, tokens=reads.get("tokens"),
            ).dto
            markets.append(morpho_dto)
        except (MorphoAPIError, NormalizationError, LookupError, ValueError) as exc:
            errors.append(f"morpho: {_err(exc)}")

        # 3. Pendle: API item normalised with readTokens().
        pendle_dto = pendle_raw = None
        try:
            listing = self.pendle.list_markets(is_active=True)
            found = find_listing_item(listing, "address", pendle_addr)
            if found is None:
                raise LookupError(f"market not in Pendle listing ({listing.stop_reason}; "
                                  f"{[str(x) for x in listing.errors]})")
            item, pendle_raw = found
            api_raws.append(pendle_raw)
            pendle_dto = normalize_pendle_market(
                item, SourceRef.from_response(pendle_raw), onchain=reads.get("pendle_tokens")
            ).dto
            markets.append(pendle_dto)
        except (PendleAPIError, NormalizationError, LookupError, ValueError) as exc:
            errors.append(f"pendle: {_err(exc)}")

        # 4. Persist what was obtained.
        self._save_api_evidence(api_raws, written)
        self._flush_rpc_evidence(written)
        written["markets"] = self.sqlite.save_markets(markets)

        series: list[list[Observation]] = []
        if morpho_dto is not None:
            f, entity = morpho_dto.morpho, morpho_market_key(market_id)
            if "state" in reads:
                block_ts, st = pinned.timestamp, reads["state"]
                retrieved = self._rpc_fetched_at(st.ref.evidence_id, now)
                for metric, value, unit in (
                    (TimeseriesMetric.TOTAL_SUPPLY_ASSETS, f.total_supply_assets, SeriesUnit.TOKEN),
                    (TimeseriesMetric.TOTAL_BORROW_ASSETS, f.total_borrow_assets, SeriesUnit.TOKEN),
                    (TimeseriesMetric.UTILIZATION, f.utilization, SeriesUnit.RATIO),
                ):
                    series.append([_snapshot(entity, metric, block_ts, value, unit, st.ref.source_id,
                                             retrieved, st.block_number)])
            if "oracle" in reads:
                o = reads["oracle"]
                value = f.oracle_price.value if f.oracle_price is not None else None
                series.append([_snapshot(entity, TimeseriesMetric.ORACLE_PRICE, pinned.timestamp, value,
                                         SeriesUnit.TOKEN, o.ref.source_id,
                                         self._rpc_fetched_at(o.ref.evidence_id, now), o.block_number,
                                         PricingMethod.MORPHO_ORACLE)])
            if apys_raw is not None:
                for metric, value in ((TimeseriesMetric.SUPPLY_APY, f.supply_apy),
                                      (TimeseriesMetric.BORROW_APY, f.borrow_apy)):
                    series.append([_snapshot(entity, metric, apys_raw.retrieved_at, value, SeriesUnit.RATIO,
                                             apys_raw.source_id, apys_raw.retrieved_at)])
        if pendle_dto is not None:
            p, entity = pendle_dto.pendle, pendle_market_key(pendle_addr)
            for metric, value, unit in (
                (TimeseriesMetric.PT_IMPLIED_APY, p.implied_apy, SeriesUnit.RATIO),
                (TimeseriesMetric.UNDERLYING_APY, p.underlying_apy, SeriesUnit.RATIO),
                (TimeseriesMetric.LIQUIDITY_USD, p.liquidity_usd, SeriesUnit.USD),
            ):
                series.append([_snapshot(entity, metric, pendle_raw.retrieved_at, value, unit,
                                         pendle_raw.source_id, pendle_raw.retrieved_at)])
        points = 0
        for rows in series:
            try:
                self.parquet.write_observations(rows, DataMode.REAL)
                points += len(rows)
            except ObservationError as exc:
                errors.append(f"parquet {rows[0].metric}: {_err(exc)}")
        written["points"] = points

    def _rpc_fetched_at(self, evidence_id: str, default: datetime) -> datetime:
        ev = self._rpc.evidence.get(evidence_id) if self._rpc is not None else None
        return ev.fetched_at if ev is not None else default

    def refresh_metadata(self) -> TaskResult:
        """Market lists + join -> candidate strategies. Never promotes, never demotes."""
        def run(written: dict[str, int], errors: list[str]) -> None:
            now = self.clock()
            raws: RawItems = {}
            morpho_markets, _, issues = load_morpho(self.morpho, 100, raws)
            pendle_markets, _, p_issues = load_pendle(self.pendle, 30, raws)
            errors.extend(i for i in issues + p_issues if "listing" in i)  # per-item issues are logged only
            result = find_candidate_strategies(morpho_markets, pendle_markets, as_of=now)
            keep = []
            for c in (*result.candidates, *result.expired):
                stored = self.sqlite.get_strategy(c.strategy.strategy_id)
                if stored is None or stored.status not in _VERIFIED_STATUSES:
                    keep.append(c.strategy)
            pages = {id(raw): raw for _, raw in raws.values()}
            self._save_api_evidence(list(pages.values()), written)
            written["strategies"] = self.sqlite.save_strategies(keep)
        return self._run("refresh_metadata", run)

    def verify_onchain(self, strategy: StrategyDTO | None = None) -> TaskResult:
        """Re-verify the strategy at one pinned block and promote/demote it."""
        def run(written: dict[str, int], errors: list[str]) -> None:
            t, now = self.target(strategy), self.clock()
            raws: RawItems = {}
            morpho_markets, _, issues = load_morpho(self.morpho, 100, raws, collateral_token=t.pt_address)
            pendle_markets, _, p_issues = load_pendle(self.pendle, 30, raws)
            errors.extend(i for i in issues + p_issues if "listing" in i)
            keys = (morpho_market_key(t.morpho_market_id), pendle_market_key(t.pendle_market_address))
            missing = [k for k in keys if k not in raws]
            if missing:
                errors.append(f"market(s) not in API listings: {missing}")
                return
            by_key = {m.market_key: m for m in morpho_markets + pendle_markets}
            joined = find_candidate_strategies([by_key[keys[0]]], [by_key[keys[1]]], as_of=now)
            cands = joined.candidates or joined.expired
            if not cands:
                errors.append("markets no longer join (collateral != PT)")
                return
            try:
                rpc, rpc_error = self.rpc(), None
            except EthereumRPCError as exc:
                rpc, rpc_error = None, _err(exc)
            promoted, v, problems, _ = verify_and_promote(
                cands[0], by_key[keys[0]], by_key[keys[1]], raws, self.pendle, self.morpho, rpc, rpc_error, now
            )
            if v.error:
                errors.append(f"rpc: {v.error}")
            self._save_api_evidence([raws[k][1] for k in keys], written)
            self._flush_rpc_evidence(written)
            written["strategies"] = self.sqlite.save_strategies([promoted])
            written["relations"] = self.sqlite.save_relations(v.relations)
            if promoted.status != StrategyStatus.VERIFIED_ACTIVE:
                errors.append(f"status {promoted.status.value}: {promoted.status_reason}")
        return self._run("verify_onchain", run)

    def backfill_history(self, strategy: StrategyDTO | None = None, days: int = 30) -> TaskResult:
        def run(written: dict[str, int], errors: list[str]) -> None:
            t = self.target(strategy)
            loan_address = strategy.loan_asset.address if strategy else self._stored_loan_address(t)
            decimals = None
            try:
                rpc = self.rpc()
                decimals = int(read_erc20_decimals(rpc, loan_address, rpc.pin_block()).value)
            except EthereumRPCError as exc:
                errors.append(f"rpc loan decimals: {_err(exc)}")
            result = run_backfill(
                {"pendleMarketAddress": t.pendle_market_address, "morphoMarketId": t.morpho_market_id},
                pendle=self.pendle, morpho=self.morpho, parquet=self.parquet, sqlite=self.sqlite,
                loan_token=TokenRef(chain_id=1, address=loan_address, decimals=decimals),
                now=self.clock(), days=days, time_frame="day",
            )
            written["points"] = sum(r.written for r in result.series)
            errors.extend(f"{s.name}: {s.status} ({s.reason})" for s in result.sources if s.status != "written")
            if not result.wrote(PENDLE_SOURCE):
                errors.append("pendle history not written")
        return self._run("backfill_history", run)

    def _stored_loan_address(self, t: Target) -> str:
        stored = self.sqlite.get_market(morpho_market_key(t.morpho_market_id))
        if stored is not None:
            return stored.morpho.loan_token.address
        m = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        return normalize_address(m["loanTokenAddress"])

    def apply_maturity_task(self, strategy: StrategyDTO | None = None) -> TaskResult:
        def run(written: dict[str, int], errors: list[str]) -> None:
            stored = strategy or self._stored_strategy()
            if stored is None:
                errors.append("no stored strategy")
                return
            updated = apply_maturity(stored, self.clock())
            written["strategies"] = self.sqlite.save_strategies([updated]) if updated is not stored else 0
        return self._run("apply_maturity", run)

    def _stored_strategy(self) -> StrategyDTO | None:
        t = self.target()
        pt = self.sqlite.get_market(pendle_market_key(t.pendle_market_address))
        if pt is None:
            return None
        return self.sqlite.get_strategy(make_strategy_id(1, t.morpho_market_id, pt.pendle.pt.address))

    # -- scheduling / serving -------------------------------------------------

    def tasks(self) -> dict[str, Callable[[], TaskResult]]:
        return {
            "refresh_metadata": self.refresh_metadata,
            "verify_onchain": self.verify_onchain,
            "refresh_current": self.refresh_current,
            "backfill_history": self.backfill_history,
            "apply_maturity": self.apply_maturity_task,
        }

    def run_due(self, now: datetime, last_runs: dict[str, datetime]) -> list[TaskResult]:
        """Run tasks whose interval elapsed since `last_runs[name]` (updated in place)."""
        results = []
        for name, task in self.tasks().items():
            last = last_runs.get(name)
            if last is None or (now - last).total_seconds() >= TASK_INTERVALS[name]:
                results.append(task())
                last_runs[name] = now
        return results

    def get_with_freshness(self, kind: str, key: str) -> tuple[Any | None, Freshness]:
        """kind "market" (key = market key) or "strategy" (key = strategy id).

        Returns the stored DTO unchanged with its freshness; (None, stale) when absent.
        """
        ttl_kind = _SERVED_KINDS[kind]
        dto = self.sqlite.get_market(key) if kind == "market" else self.sqlite.get_strategy(key)
        return dto, freshness(dto.as_of if dto is not None else None, ttl_kind, self.clock())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print(result: TaskResult) -> None:
    secs = (result.finished_at - result.started_at).total_seconds()
    print(f"[{result.finished_at:%H:%M:%SZ}] {result.name}: {'ok' if result.ok else 'FAILED'} "
          f"({secs:.1f}s) written={result.written}")
    for e in result.errors:
        print(f"    error: {e}")


def main(argv: list[str] | None = None) -> int:
    from app.adapters.morpho_api import MorphoApiClient
    from app.adapters.pendle_api import PendleApiClient

    parser = argparse.ArgumentParser(description="Refresh demo strategy data")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="run every task once")
    mode.add_argument("--loop", action="store_true", help="run due tasks forever (Ctrl-C to stop)")
    mode.add_argument("--task", choices=sorted(TASK_INTERVALS))
    parser.add_argument("--sleep", type=int, default=30, help="--loop: seconds between checks")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    with MorphoApiClient() as morpho, PendleApiClient() as pendle, SqliteStore() as sqlite:
        r = Refresher(morpho=morpho, pendle=pendle, sqlite=sqlite, parquet=ParquetStore())
        if args.task:
            result = r.tasks()[args.task]()
            _print(result)
            return 0 if result.ok else 1
        if args.once:
            results = [task() for task in r.tasks().values()]
            for result in results:
                _print(result)
            return 0 if all(x.ok for x in results) else 1
        last_runs: dict[str, datetime] = {}
        try:
            while True:
                for result in r.run_due(r.clock(), last_runs):
                    _print(result)
                time.sleep(args.sleep)
        except KeyboardInterrupt:
            print("stopped")
            return 0


if __name__ == "__main__":
    sys.exit(main())
