"""PR2 volatility calibration from an observed Morpho ORACLE_PRICE series.

Method ORACLE_DAILY_LOG_RETURNS_30D_V1:
  * grid: 31 UTC-midnight target times ending at the last midnight <= as_of;
  * sample: for each target, the latest valid point observed in
    [target - 1 h, target]; nothing outside this window is used, nothing is
    interpolated and a missing target makes the calibration UNAVAILABLE;
  * a point is valid only with a positive value, a block number and an RPC
    `price()` read of this market's oracle at that block whose raw value scales
    exactly to the stored value;
  * 30 log returns ln(P_k / P_k-1), sample standard deviation (n - 1),
    annualized by sqrt(365); drift is fixed at zero, not estimated;
  * points after as_of, or proven by evidence fetched after as_of, are ignored.

The result describes past oracle variability. It is not a forecast and does
not turn simulated breach frequencies into objective liquidation probabilities.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext

from pydantic import ValidationError

from ...models.evidence import DataMode, DataStatus, EvidenceDTO, SourceKind, scale_raw
from ...models.market import MORPHO_ORACLE_PRICE_SCALE, MarketDTO, PricingMethod
from ...models.timeseries import SeriesUnit, SubjectKind, TimeseriesDTO, TimeseriesMetric
from .models import (
    CALIBRATION_ANNUALIZATION_DAYS,
    CALIBRATION_RETURNS,
    CALIBRATION_SAMPLING_SECONDS,
    CALIBRATION_TOLERANCE_SECONDS,
    CalibrationPoint,
    OracleCalibration,
)

RPC_SOURCE_ID = "ethereum_rpc"
ORACLE_PRICE_METHOD = "eth_call price()"
_SAMPLING = timedelta(seconds=CALIBRATION_SAMPLING_SECONDS)
_TOLERANCE = timedelta(seconds=CALIBRATION_TOLERANCE_SECONDS)
_PRECISION = 60
_OUTPUT_QUANTUM = Decimal("1e-18")


class MonteCarloDataUnavailable(ValueError):
    """Inputs are absent or unproven: the caller reports UNAVAILABLE, never a fallback."""

    def __init__(self, missing_fields: list[str]):
        self.missing_fields = sorted(set(missing_fields))
        super().__init__("Monte Carlo inputs unavailable: " + ", ".join(self.missing_fields))


def _day_floor(value: datetime) -> datetime:
    value = value.astimezone(timezone.utc)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def is_sampling_candidate(timestamp: datetime) -> bool:
    """True when `timestamp` falls inside the window preceding a UTC midnight."""
    day = _day_floor(timestamp)
    return timestamp == day or day + _SAMPLING - _TOLERANCE <= timestamp


def sampling_targets(as_of: datetime) -> list[datetime]:
    """The 31 target times, oldest first; the newest is the last midnight <= as_of."""
    end = _day_floor(as_of)
    return [end - _SAMPLING * k for k in range(CALIBRATION_RETURNS, -1, -1)]


def select_oracle_series(series, market: MarketDTO) -> tuple[TimeseriesDTO | None, list[str]]:
    """Pick this market's RPC oracle series; never a spot or other-source series."""
    same_market = [s for s in series if s.subject_kind == SubjectKind.MARKET
                   and s.subject_id == market.market_id and s.chain_id == market.chain_id]
    oracle = [s for s in same_market if s.metric == TimeseriesMetric.ORACLE_PRICE]
    rpc = [s for s in oracle if s.source_id == RPC_SOURCE_ID]
    if len(rpc) == 1:
        return rpc[0], []
    if len(rpc) > 1:
        return None, ["oracle_series.ambiguous"]
    return None, ["oracle_series.source" if oracle else "oracle_series"]


def _scale_exponent(market: MarketDTO) -> int | None:
    m = market.morpho
    if m.oracle_price is not None:
        return m.oracle_price.scale_exponent
    if m.loan_token.decimals is None or m.collateral_token.decimals is None:
        return None
    return MORPHO_ORACLE_PRICE_SCALE + m.loan_token.decimals - m.collateral_token.decimals


def _proof(point, records: dict[str, EvidenceDTO], market: MarketDTO, exponent: int,
           as_of: datetime) -> str | None:
    """Evidence id of an RPC price() read matching this point exactly, else None."""
    for evidence_id in point.evidence_ids:
        e = records.get(evidence_id)
        if (e is None or e.source_kind != SourceKind.RPC or e.status != DataStatus.OK
                or e.method != ORACLE_PRICE_METHOD or e.target_address != market.morpho.oracle_address
                or e.chain_id != market.chain_id or e.data_mode != market.data_mode
                or e.block_number != point.block_number or not e.block_hash
                or e.fetched_at > as_of or not e.decoded_excerpt):
            continue
        try:
            raw = int(e.decoded_excerpt.get("out0", ""))
        except ValueError:
            continue
        if raw > 0 and scale_raw(raw, exponent) == point.value:
            return evidence_id
    return None


def calibrate_oracle_volatility(
    series: TimeseriesDTO | None,
    market: MarketDTO,
    *,
    evidence,
    as_of: datetime,
) -> OracleCalibration:
    """Return a verified calibration or raise MonteCarloDataUnavailable."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if series is None:
        raise MonteCarloDataUnavailable(["oracle_series"])
    m = market.morpho
    if m is None:
        raise MonteCarloDataUnavailable(["market.morpho"])
    missing = []
    # Identity: only a Morpho oracle series for this market, in this loan token.
    if series.metric != TimeseriesMetric.ORACLE_PRICE or series.pricing_method != PricingMethod.MORPHO_ORACLE:
        missing.append("oracle_series.metric")
    if (series.subject_kind != SubjectKind.MARKET or series.subject_id != market.market_id
            or series.chain_id != market.chain_id):
        missing.append("oracle_series.subject")
    if series.unit != SeriesUnit.TOKEN or series.quote_asset is None or not series.quote_asset.same_asset(m.loan_token):
        missing.append("oracle_series.quote_asset")
    if series.source_id != RPC_SOURCE_ID:
        missing.append("oracle_series.source")
    if series.data_mode != DataMode.REAL or market.data_mode != DataMode.REAL:
        missing.append("oracle_series.data_mode")
    exponent = _scale_exponent(market)
    if exponent is None:
        missing.append("oracle_series.scale_exponent")
    if missing:
        raise MonteCarloDataUnavailable(missing)

    records = {e.evidence_id: e for e in evidence}
    future = sum(1 for p in series.points if p.timestamp > as_of)
    usable = [p for p in series.points if p.timestamp <= as_of]
    skipped = 0
    samples: list[CalibrationPoint] = []
    for target in sampling_targets(as_of):
        candidates = [p for p in usable if target - _TOLERANCE <= p.timestamp <= target]
        chosen = None
        for point in reversed(candidates):  # latest first
            valid = (point.value is not None and point.value > 0 and point.status == DataStatus.OK
                     and point.block_number is not None)
            evidence_id = _proof(point, records, market, exponent, as_of) if valid else None
            if evidence_id is not None:
                chosen = CalibrationPoint(target_time=target, observed_at=point.timestamp,
                                          block_number=point.block_number,
                                          oracle_price_loan_per_pt=point.value, evidence_id=evidence_id)
                break
            skipped += 1
        if chosen is None:
            missing.append("oracle_series.sample:" + target.isoformat().replace("+00:00", "Z"))
        else:
            samples.append(chosen)
    if missing:
        raise MonteCarloDataUnavailable(missing)
    blocks = [p.block_number for p in samples]
    if any(b <= a for a, b in zip(blocks, blocks[1:])):
        raise MonteCarloDataUnavailable(["oracle_series.block_order"])

    with localcontext() as ctx:
        ctx.prec = _PRECISION
        try:
            returns = [(b.oracle_price_loan_per_pt / a.oracle_price_loan_per_pt).ln()
                       for a, b in zip(samples, samples[1:])]
            n = Decimal(len(returns))
            mean = sum(returns, Decimal(0)) / n
            variance = sum(((r - mean) ** 2 for r in returns), Decimal(0)) / (n - 1)
            stdev = variance.sqrt()
            annual = stdev * Decimal(CALIBRATION_ANNUALIZATION_DAYS).sqrt()
        except (InvalidOperation, ArithmeticError):
            raise MonteCarloDataUnavailable(["oracle_series.returns"]) from None
    try:
        return OracleCalibration(
            series_id=series.series_id, market_id=market.market_id, oracle_address=m.oracle_address,
            collateral_token_address=m.collateral_token.address, loan_token_address=m.loan_token.address,
            window_start=samples[0].target_time, window_end=samples[-1].target_time,
            daily_log_return_mean=mean.quantize(_OUTPUT_QUANTUM),
            daily_log_return_stdev=stdev.quantize(_OUTPUT_QUANTUM),
            annual_volatility=annual.quantize(_OUTPUT_QUANTUM),
            skipped_candidate_points=skipped, ignored_future_points=future,
            points=samples, evidence_ids=sorted({p.evidence_id for p in samples}),
        )
    except ValidationError:
        # e.g. annualized volatility above the simulation bound of 5 (500 %).
        raise MonteCarloDataUnavailable(["oracle_series.volatility_out_of_range"]) from None
