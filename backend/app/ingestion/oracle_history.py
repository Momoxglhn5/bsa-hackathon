"""Retrospective Morpho oracle history: IOracle.price() at past blocks via eth_call.

One read per UTC midnight: the last block whose timestamp is <= that midnight,
which is the sampling grid of the Monte Carlo calibration. Every read keeps its
RPC evidence (block number and hash, raw value), and the Parquet row keeps the
block number so the analytics provider can re-link the proof.

Past-block eth_call needs historical state: most full or public nodes only keep
recent blocks, so an archive-capable ETHEREUM_RPC_URL is usually required. A
failing read is reported, never replaced; the run stops after a few
consecutive failures instead of hammering the RPC. No transaction, no signing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.adapters.ethereum_rpc import (
    EthereumRPCError,
    PinnedBlock,
    read_erc20_decimals,
    read_morpho_market_params,
    read_oracle_price,
)
from app.ingestion.normalize import morpho_market_key, oracle_price
from app.models.evidence import DataMode, normalize_address, normalize_bytes32
from app.models.market import PricingMethod, TokenRef
from app.models.timeseries import SeriesUnit, TimeseriesMetric
from app.storage.parquet_store import Observation

SLOT_SECONDS = 12
# First proof-of-stake block: from here on, consecutive blocks are >= 12 s apart.
MERGE_TIMESTAMP = datetime(2022, 9, 15, 6, 42, 59, tzinfo=timezone.utc)
MAX_DAYS = 60


@dataclass
class OracleSample:
    target_time: datetime
    block_number: int | None = None
    block_time: datetime | None = None
    value: str | None = None
    error: str | None = None


@dataclass
class OracleBackfillReport:
    market_id: str
    oracle_address: str | None = None
    samples: list[OracleSample] = field(default_factory=list)
    written_points: int = 0
    written_evidence: int = 0
    stopped_reason: str | None = None

    @property
    def ok_count(self) -> int:
        return sum(1 for s in self.samples if s.value is not None)


def block_at_or_before(rpc, target: datetime, latest: PinnedBlock) -> PinnedBlock:
    """Last block with timestamp <= target (post-merge), by bracketing then bisection."""
    if target >= latest.timestamp:
        return latest
    if target < MERGE_TIMESTAMP:
        raise ValueError("targets before the merge are not supported")
    # Blocks are >= 12 s apart, so this block is at or before the target.
    lo = rpc.pin_block(max(0, latest.number - math.ceil((latest.timestamp - target).total_seconds() / SLOT_SECONDS)))
    if lo.timestamp > target:
        raise ValueError(f"block {lo.number} after target {target.isoformat()}: slot assumption broken")
    step = 64
    while True:
        hi = rpc.pin_block(min(latest.number, lo.number + step))
        if hi.timestamp > target:
            break
        lo, step = hi, step * 2
    while hi.number - lo.number > 1:
        mid = rpc.pin_block((lo.number + hi.number) // 2)
        if mid.timestamp <= target:
            lo = mid
        else:
            hi = mid
    return lo


def daily_targets(now: datetime, days: int) -> list[datetime]:
    """The last `days` UTC midnights <= now, oldest first."""
    end = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return [end - timedelta(days=k) for k in range(days - 1, -1, -1)]


def run_oracle_backfill(
    rpc,
    *,
    market_id: str,
    sqlite,
    parquet,
    now: datetime,
    days: int = 31,
    expected_collateral: str | None = None,
    expected_loan: str | None = None,
    dry_run: bool = False,
    max_consecutive_failures: int = 3,
) -> OracleBackfillReport:
    """Read, prove and store one oracle price per UTC midnight over `days` midnights."""
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_DAYS}")
    market_id = normalize_bytes32(market_id)
    report = OracleBackfillReport(market_id=market_id)
    latest = rpc.pin_block()
    params = read_morpho_market_params(rpc, market_id, latest)
    if expected_collateral and params.collateral_token != normalize_address(expected_collateral):
        raise ValueError("market collateral is not the expected PT")
    if expected_loan and params.loan_token != normalize_address(expected_loan):
        raise ValueError("market loan token is not the expected loan token")
    report.oracle_address = params.oracle
    collateral = TokenRef(chain_id=1, address=params.collateral_token,
                          decimals=int(read_erc20_decimals(rpc, params.collateral_token, latest).value))
    loan = TokenRef(chain_id=1, address=params.loan_token,
                    decimals=int(read_erc20_decimals(rpc, params.loan_token, latest).value))

    rows: list[Observation] = []
    failures = 0
    # Save available recent history before reaching pre-deployment/non-archive
    # blocks; keep the consecutive-failure limit to bound RPC traffic.
    for target in reversed(daily_targets(now, days)):
        sample = OracleSample(target_time=target)
        report.samples.append(sample)
        try:
            pinned = block_at_or_before(rpc, target, latest)
            sample.block_number, sample.block_time = pinned.number, pinned.timestamp
            read = read_oracle_price(rpc, params.oracle, pinned)
            dto, reason = oracle_price(read, collateral, loan)
            if dto is None:
                raise ValueError(f"oracle price not normalisable: {reason}")
        except (EthereumRPCError, ValueError) as exc:
            sample.error = f"{type(exc).__name__}: {exc}"[:300]
            failures += 1
            if failures >= max_consecutive_failures:
                report.stopped_reason = (f"{failures} consecutive failures; past-block reads usually "
                                         "need an archive-capable ETHEREUM_RPC_URL")
                break
            continue
        failures = 0
        sample.value = str(dto.value)
        fetched_at = rpc.evidence[read.ref.evidence_id].fetched_at
        rows.append(Observation(
            entity_id=morpho_market_key(market_id), metric=TimeseriesMetric.ORACLE_PRICE.value,
            ts_utc=pinned.timestamp, value_decimal=dto.value, unit=SeriesUnit.TOKEN.value,
            source_id=read.ref.source_id, retrieved_at=fetched_at, block_number=pinned.number,
            pricing_method=PricingMethod.MORPHO_ORACLE.value,
        ))
    if not dry_run:
        report.written_evidence = sqlite.save_evidence(rpc.evidence.values())
        if rows:
            rows.sort(key=lambda row: row.ts_utc)
            parquet.write_observations(rows, DataMode.REAL)
        report.written_points = len(rows)
    return report
