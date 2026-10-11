"""Spot equity and oracle health are deliberately separate calculations."""
from decimal import Decimal, localcontext
from datetime import timedelta

from app.analytics.dataset import AnalyticsSnapshot
from app.models.evidence import ContractModel, PreciseDecimal
from app.models.market import MarketPriceDTO
from app.models.position import PositionDTO

BAD = {"MISSING", "ERROR", "MISMATCH", "STALE"}


def fresh_price(snapshot, price, now, *, data_mode=None):
    if price is None or price.status in BAD or price.observed_at > now or now-price.observed_at > timedelta(minutes=5):
        return False
    records = {e.evidence_id: e for e in snapshot.evidence}
    return bool(price.evidence_ids) and all(
        (e := records.get(i)) is not None and e.status not in BAD
        and e.chain_id == price.chain_id and e.fetched_at <= now
        and now-e.fetched_at <= timedelta(minutes=5)
        and (data_mode is None or e.data_mode == data_mode)
        for i in price.evidence_ids)


def usd_price(price: MarketPriceDTO | None):
    if price is None or price.quote_unit != "USD" or price.status in BAD:
        return None
    return price.value


class Valuation(ContractModel):
    collateral_value_usd: PreciseDecimal | None = None
    debt_value_usd: PreciseDecimal | None = None
    equity_usd: PreciseDecimal | None = None
    gross_exposure_usd: PreciseDecimal | None = None
    oracle_collateral_value_loan_units: PreciseDecimal | None = None
    lltv_ratio: PreciseDecimal | None = None
    ltv_ratio: PreciseDecimal | None = None
    health_factor: PreciseDecimal | None = None
    liquidatable: bool | None = None


def compatible_data_modes(snapshot, position):
    """Check the provenance of quantities and valuation inputs without changing DTOs."""
    market = snapshot.market(position)
    strategy = snapshot.strategy(position.strategy_id)
    mode = position.data_mode
    if market.data_mode != mode or strategy.data_mode != mode:
        return False
    records = {e.evidence_id: e for e in snapshot.evidence}
    ids = set(position.evidence_ids) | set(market.evidence_ids)
    for price in (market.morpho.collateral_price, market.morpho.loan_price, market.morpho.oracle_price):
        if price is not None:
            ids.update(price.evidence_ids)
    return all(records[i].data_mode == mode for i in ids if i in records)


def value_position(snapshot: AnalyticsSnapshot, p: PositionDTO, oracle_change=Decimal(0), *, now=None) -> Valuation:
    now = now or snapshot.as_of
    if not compatible_data_modes(snapshot, p):
        return Valuation()
    m = snapshot.market(p).morpho
    cp = usd_price(m.collateral_price) if fresh_price(snapshot, m.collateral_price, now, data_mode=snapshot.market(p).data_mode) else None
    lp = usd_price(m.loan_price) if fresh_price(snapshot, m.loan_price, now, data_mode=snapshot.market(p).data_mode) else None
    c, d = p.collateral_amount, p.borrow_amount
    if (p.status in BAD or snapshot.market(p).status in BAD or p.as_of > now
            or (p.kind == "ONCHAIN" and now-p.as_of > timedelta(minutes=5))):
        return Valuation()
    with localcontext() as ctx:
        ctx.prec = 60
        cv = c * cp if c is not None and cp is not None else None
        dv = d * lp if d is not None and lp is not None else None
        known = cv is not None and dv is not None
        op = m.oracle_price
        oc = c * op.value * (1 + oracle_change) if c is not None and op is not None and fresh_price(snapshot, op, now, data_mode=snapshot.market(p).data_mode) else None
        hf = oc * m.lltv / d if oc is not None and d is not None and d > 0 else None
        ltv = d / oc if d is not None and oc is not None and oc > 0 else None
        liquidatable = (False if d == 0 else (oc * m.lltv <= d if oc is not None and d is not None else None))
        return Valuation(collateral_value_usd=cv, debt_value_usd=dv,
                         equity_usd=cv-dv if known else None,
                         gross_exposure_usd=cv+dv if known else None,
                         oracle_collateral_value_loan_units=oc, lltv_ratio=m.lltv,
                         ltv_ratio=ltv, health_factor=hf, liquidatable=liquidatable)
