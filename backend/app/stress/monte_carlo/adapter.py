"""Join Data DTOs to a Monte Carlo request without inventing any input.

Amounts, prices, LLTV and expiry come from Data. The client only chooses the
simulation settings and the volatility source:
  * USER_ASSUMPTION: client-supplied volatility/drift (PR1 semantics);
  * OBSERVED_ORACLE_SERIES: volatility calibrated by `calibration.py` on this
    market's RPC-proven oracle series; drift fixed at zero.
REAL_DATA is reserved for a verified on-chain position with an observed
calibration. A SIMULATED position, DEMO data or assumed volatility stays
HYPOTHETICAL. Missing or unproven data raises MonteCarloDataUnavailable; the
synthetic fixture is never substituted.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...models.evidence import DataMode, DataStatus, SourceKind
from ...models.market import MarketDTO, PricingMethod, Protocol, QuoteUnit
from ...models.position import PositionDTO, PositionKind
from ...models.strategy import StrategyDTO, StrategyStatus
from .calibration import MonteCarloDataUnavailable, calibrate_oracle_volatility, select_oracle_series
from .models import (
    DebtModel,
    InputScope,
    McStatus,
    MonteCarloProvenance,
    MonteCarloRequest,
    MonteCarloResult,
    MODEL_VERSION,
    PreciseDecimal,
    SimulationParams,
    SourceMode,
)

__all__ = ["MonteCarloDataRequest", "MonteCarloDataUnavailable", "build_monte_carlo_request",
           "unavailable_result"]

# Below this, the oracle barely moved: often a deterministic (e.g. linear
# discount) PT oracle, so low simulated breach counts say little about markets.
_LOW_OBSERVED_VOLATILITY = Decimal("0.01")


class MonteCarloDataRequest(BaseModel):
    """Client settings only; position amounts and prices come from Data."""
    model_config = ConfigDict(extra="forbid")
    strategy_id: str
    position_id: str
    horizon_days: Literal[1, 3, 7]
    num_paths: int = Field(default=10000, strict=True, ge=1000, le=50000)
    seed: int = Field(default=42, strict=True, ge=0)
    volatility_source: SourceMode = SourceMode.USER_ASSUMPTION
    annual_volatility: PreciseDecimal | None = Field(default=None, ge=0, le=5)
    annual_drift: PreciseDecimal | None = None
    debt_model: DebtModel = DebtModel.FROZEN_DEBT
    borrow_apr: PreciseDecimal | None = None

    @model_validator(mode="after")
    def volatility_matches_source(self):
        if self.volatility_source == SourceMode.USER_ASSUMPTION:
            if self.annual_volatility is None:
                raise ValueError("USER_ASSUMPTION requires annual_volatility")
        elif self.annual_volatility is not None or self.annual_drift not in (None, 0):
            raise ValueError("OBSERVED_ORACLE_SERIES calibrates volatility; drift is fixed at zero")
        if self.debt_model == DebtModel.CONSTANT_APR and self.borrow_apr is None:
            raise ValueError("borrow_apr is required for CONSTANT_APR")
        return self


def unavailable_result(settings: MonteCarloDataRequest, as_of: datetime,
                       missing_fields: list[str]) -> MonteCarloResult:
    """Explicit UNAVAILABLE result: no data, no fabricated metric."""
    return MonteCarloResult(
        status=McStatus.UNAVAILABLE, as_of=as_of, data=None,
        provenance=MonteCarloProvenance(calibration=settings.volatility_source, evidence_ids=[]),
        missing_fields=sorted(set(missing_fields)),
        warnings=["NO_SIMULATION_RUN", "NO_FIXTURE_SUBSTITUTED"],
    )


def build_monte_carlo_request(
    position: PositionDTO,
    market: MarketDTO,
    strategy: StrategyDTO,
    *,
    settings: MonteCarloDataRequest,
    as_of: datetime,
    evidence=(),
    series=(),
    replay: bool = False,
) -> MonteCarloRequest:
    """Map validated, fresh DTOs (and, if requested, a verified calibration)."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError('as_of must be timezone-aware')
    if settings.strategy_id != strategy.strategy_id or settings.position_id != position.position_id:
        raise ValueError('Requested strategy/position does not match Data')
    if market.protocol != Protocol.MORPHO_BLUE or market.morpho is None:
        raise ValueError('A Morpho market is required')
    m = market.morpho
    if not (position.chain_id == market.chain_id == strategy.chain_id == 1):
        raise ValueError('All inputs must be Ethereum mainnet chain_id=1')
    if not (position.data_mode == market.data_mode == strategy.data_mode):
        raise ValueError('Cannot mix REAL and DEMO inputs')
    if not (position.market_id == market.market_id == strategy.morpho_market_id):
        raise ValueError('Position/market/strategy market IDs differ')
    if position.strategy_id is not None and position.strategy_id != strategy.strategy_id:
        raise ValueError('Position belongs to another strategy')
    if not (position.collateral_asset.same_asset(strategy.pt)
            and position.collateral_asset.same_asset(m.collateral_token)):
        raise ValueError('Position collateral must be the strategy PT')
    if not (position.loan_asset.same_asset(strategy.loan_asset)
            and position.loan_asset.same_asset(m.loan_token)):
        raise ValueError('Loan asset mismatch')

    missing = []
    bad = {DataStatus.ERROR, DataStatus.MISSING, DataStatus.MISMATCH, DataStatus.STALE}
    if position.status in bad:
        missing.append('position.status')
    if market.status in bad:
        missing.append('market.status')
    if strategy.data_status in bad:
        missing.append('strategy.data_status')
    if position.collateral_amount is None:
        missing.append('position.collateral_amount')
    if position.borrow_amount is None:
        missing.append('position.borrow_amount')
    if strategy.expiry is None:
        missing.append('strategy.expiry')
    for name, stamp, ttl in (
        ('position.as_of', position.as_of, 300),
        ('market.as_of', market.as_of, 300),
        ('strategy.as_of', strategy.as_of, 86400),
    ):
        if stamp > as_of or as_of - stamp > timedelta(seconds=ttl):
            missing.append(name)

    op = m.oracle_price
    if op is None:
        missing.append('market.morpho.oracle_price')
    else:
        if (op.pricing_method != PricingMethod.MORPHO_ORACLE or op.quote_unit != QuoteUnit.TOKEN
                or not op.base_asset.same_asset(strategy.pt)
                or op.quote_asset is None or not op.quote_asset.same_asset(position.loan_asset)
                or op.oracle_address != m.oracle_address):
            raise ValueError('Oracle price must quote this PT in this loan token')
        if op.value <= 0 or op.status in bad:
            missing.append('market.morpho.oracle_price.value')
        if op.observed_at > as_of or as_of - op.observed_at > timedelta(seconds=300):
            missing.append('market.morpho.oracle_price.observed_at')
        # DEMO markets carry no on-chain block; the same-block proof applies to REAL reads.
        if market.data_mode == DataMode.REAL and market.block_number != op.block_number:
            missing.append('market.oracle_block_consistency')

    records = {e.evidence_id: e for e in evidence}
    ids = set(position.evidence_ids) | set(market.evidence_ids) | set(strategy.evidence_ids)
    if op is not None:
        ids.update(op.evidence_ids)
    for eid in ids:
        ev = records.get(eid)
        if (ev is None or ev.chain_id != market.chain_id or ev.data_mode != market.data_mode
                or ev.status in bad or ev.fetched_at > as_of):
            missing.append('evidence:' + eid)

    if market.data_mode == DataMode.REAL:
        if market.status not in (DataStatus.OK, DataStatus.PARTIAL):
            missing.append('market.verification')
        if strategy.status not in (StrategyStatus.VERIFIED_ACTIVE, StrategyStatus.EXPIRED):
            missing.append('strategy.verification')
        if op is not None:
            proofs = [records[eid] for eid in op.evidence_ids if eid in records]
            if not any(e.source_kind == SourceKind.RPC and e.target_address == m.oracle_address
                       and e.block_number == op.block_number and e.block_hash
                       and as_of - e.fetched_at <= timedelta(seconds=300) for e in proofs):
                missing.append('oracle.rpc_evidence')
        if position.kind == PositionKind.ONCHAIN:
            if position.status not in (DataStatus.OK, DataStatus.PARTIAL):
                missing.append('position.verification')
            if position.block_number != market.block_number:
                missing.append('position.market_block_consistency')
            proofs = [records[eid] for eid in position.evidence_ids if eid in records]
            if not any(e.source_kind == SourceKind.RPC and e.block_number == position.block_number
                       and e.target_address == m.morpho_address and e.block_hash for e in proofs):
                missing.append('position.rpc_evidence')

    calibration = None
    if settings.volatility_source == SourceMode.OBSERVED_ORACLE_SERIES:
        chosen, series_missing = select_oracle_series(series, market)
        try:
            if series_missing:
                raise MonteCarloDataUnavailable(series_missing)
            calibration = calibrate_oracle_volatility(chosen, market, evidence=evidence, as_of=as_of)
        except MonteCarloDataUnavailable as exc:
            missing.extend(exc.missing_fields)
    if missing:
        raise MonteCarloDataUnavailable(missing)

    warnings = ['INPUT_DATA_MODE_' + market.data_mode.value]
    if calibration is None:
        warnings.append('VOLATILITY_AND_DRIFT_ARE_USER_ASSUMPTIONS_NOT_CALIBRATED')
        simulation = SimulationParams(
            model=MODEL_VERSION, source_mode=SourceMode.USER_ASSUMPTION,
            annual_volatility=settings.annual_volatility,
            annual_drift=settings.annual_drift if settings.annual_drift is not None else 0,
            debt_model=settings.debt_model, borrow_apr=settings.borrow_apr,
        )
    else:
        warnings.append('VOLATILITY_CALIBRATED_ON_OBSERVED_ORACLE_SERIES')
        if calibration.annual_volatility < _LOW_OBSERVED_VOLATILITY:
            warnings.append('LOW_OBSERVED_ORACLE_VOLATILITY_MAY_REFLECT_DETERMINISTIC_ORACLE')
        simulation = SimulationParams(
            model=MODEL_VERSION, source_mode=SourceMode.OBSERVED_ORACLE_SERIES,
            annual_volatility=calibration.annual_volatility, annual_drift=calibration.annual_drift,
            debt_model=settings.debt_model, borrow_apr=settings.borrow_apr, calibration=calibration,
        )
        ids.update(calibration.evidence_ids)
    if position.kind == PositionKind.SIMULATED:
        warnings.append('HYPOTHETICAL_POSITION_AMOUNTS')
    if replay:
        warnings.append('FROZEN_REPLAY_NOT_LIVE')
    real_data = (market.data_mode == DataMode.REAL and position.kind == PositionKind.ONCHAIN
                 and calibration is not None)
    return MonteCarloRequest(
        strategy_id=strategy.strategy_id, position_id=position.position_id,
        snapshot_as_of=as_of,
        input_scope=InputScope.REAL_DATA if real_data else InputScope.HYPOTHETICAL,
        loan_token_symbol=position.loan_asset.symbol, loan_token_address=position.loan_asset.address,
        collateral_quantity_pt=position.collateral_amount,
        initial_oracle_price_loan_per_pt=op.value, debt_loan_units=position.borrow_amount,
        lltv=m.lltv, horizon_days=settings.horizon_days, num_paths=settings.num_paths,
        seed=settings.seed, pt_expiry=strategy.expiry, simulation=simulation,
        evidence_ids=sorted(ids), warnings=warnings,
    )
