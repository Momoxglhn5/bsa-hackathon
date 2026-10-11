"""Deterministic conditional carry; no model inference or oracle substitution."""
from datetime import timedelta
from decimal import Decimal, localcontext

from pydantic import Field

from app.analytics.valuation import BAD, value_position
from app.models.evidence import ContractModel, EvidenceId, NonNegativeDecimal, PreciseDecimal


class CarryAssumptions(ContractModel):
    pt_annual_return: PreciseDecimal = Field(gt=-1, le=100)
    borrow_apy: PreciseDecimal = Field(gt=-1, le=100)
    holding_fees_usd: NonNegativeDecimal = Field(le=10**18)
    explanation: str = Field(min_length=10, max_length=1000)


class CarryResult(ContractModel):
    status: str
    horizon_days: int
    net_carry_usd: PreciseDecimal | None = None
    collateral_return_usd: PreciseDecimal | None = None
    borrowing_cost_usd: PreciseDecimal | None = None
    pt_annual_return: PreciseDecimal | None = None
    borrow_apy: PreciseDecimal | None = None
    holding_fees_usd: NonNegativeDecimal | None = None
    basis: str
    methodology: str = "V * ((1 + annual effective rate) ** (H / 365) - 1); collateral return minus debt growth and holding fees"
    evidence_ids: tuple[EvidenceId, ...] = ()
    missing_fields: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def estimate_carry(snapshot, strategy_id, position_id, horizon_days, now, assumptions=None):
    if not 1 <= horizon_days <= 365:
        raise ValueError("horizon_days must be in 1..365")
    strategy = snapshot.strategy(strategy_id)
    position = snapshot.position(strategy_id, position_id)
    morpho = snapshot.market(position)
    pendle = next((m for m in snapshot.markets if m.pendle
                   and m.chain_id == strategy.chain_id and m.data_mode == morpho.data_mode
                   and m.market_id == strategy.pendle_market_address
                   and m.pendle.pt.same_asset(position.collateral_asset)), None)
    missing, ids = [], set()
    valuation = value_position(snapshot, position, now=now)
    if valuation.equity_usd is None:
        missing.append("fresh_usd_valuation")
    else:
        for price in (morpho.morpho.collateral_price, morpho.morpho.loan_price):
            ids.update(price.evidence_ids)
    if strategy.expiry is None or now + timedelta(days=horizon_days) >= strategy.expiry:
        missing.append("horizon_before_pt_expiry")
    if strategy.data_mode != morpho.data_mode or position.data_mode != morpho.data_mode:
        missing.append("consistent_data_mode")
    if strategy.data_mode == "REAL" and strategy.status != "VERIFIED_ACTIVE":
        missing.append("verified_strategy")
    warnings = ["CONDITIONAL_CARRY_NOT_A_FORECAST", "FIXED_QUANTITIES_AND_REFERENCE_USD_VALUE",
                "NO_ADDITIONAL_PT_YIELD_ADDED"]
    if strategy.expiry is None:
        warnings.append("PT_EXPIRY_UNKNOWN")
    elif now + timedelta(days=horizon_days) >= strategy.expiry:
        warnings.append("HORIZON_BEYOND_PT_EXPIRY")
    if assumptions is not None:
        pt_rate, debt_rate, fees = assumptions.pt_annual_return, assumptions.borrow_apy, assumptions.holding_fees_usd
        basis = "USER_ASSUMPTION"
        warnings.append("USER_ASSUMED_RATES_AND_HOLDING_FEES")
    else:
        basis = "CONSTANT_CURRENT_RATES"
        pt_rate = pendle.pendle.implied_apy if pendle else None
        debt_rate, fees = morpho.morpho.borrow_apy, Decimal(0)
        warnings += ["CURRENT_IMPLIED_PT_APY_IS_NOT_GUARANTEED_USD_RETURN",
                     "CURRENT_RATES_ASSUMED_CONSTANT", "ADDITIONAL_HOLDING_FEES_ASSUMED_ZERO"]
        records = {e.evidence_id: e for e in snapshot.evidence}
        for name, market, rate in (("pendle.implied_apy", pendle, pt_rate),
                                   ("morpho.borrow_apy", morpho, debt_rate)):
            if (market is None or rate is None or not -1 < rate <= 100
                    or market.status in BAD or not 0 <= (now-market.as_of).total_seconds() <= 300
                    or not market.evidence_ids or any(
                        (e := records.get(i)) is None or e.status in BAD
                        or e.chain_id != market.chain_id or e.data_mode != market.data_mode
                        or not 0 <= (now-e.fetched_at).total_seconds() <= 300
                        for i in market.evidence_ids)):
                missing.append(name)
            elif market is not None:
                ids.update(market.evidence_ids)
    common = dict(horizon_days=horizon_days, basis=basis, pt_annual_return=pt_rate,
                  borrow_apy=debt_rate, holding_fees_usd=fees, evidence_ids=tuple(sorted(ids)),
                  warnings=tuple(warnings))
    if missing:
        return CarryResult(status="INSUFFICIENT_DATA", missing_fields=tuple(missing), **common)
    with localcontext() as ctx:
        ctx.prec = 60
        fraction = Decimal(horizon_days) / 365
        gain = valuation.collateral_value_usd * ((1 + pt_rate) ** fraction - 1)
        cost = valuation.debt_value_usd * ((1 + debt_rate) ** fraction - 1)
        return CarryResult(status="CONDITIONAL", net_carry_usd=gain-cost-fees,
                           collateral_return_usd=gain, borrowing_cost_usd=cost, **common)
