"""Check full-position execution quotes; spot rates are never accepted."""
from datetime import timedelta

from app.analytics.valuation import BAD, usd_price, value_position


def evaluate_exit(snapshot, position, quote_id, now):
    context = next((q for q in snapshot.quotes if q.quote_id == quote_id), None)
    if context is None or context.position_id != position.position_id:
        return "QUOTE_UNAVAILABLE", None, None, "No execution quote for this position"
    q = context.quote
    external = context.requires_external_repayment_liquidity
    evidence = {e.evidence_id: e for e in snapshot.evidence}
    required_ids = q.evidence_ids + context.evidence_ids
    if not context.evidence_ids or not q.evidence_ids or any(i not in evidence or evidence[i].status in BAD or evidence[i].fetched_at > now or evidence[i].chain_id != position.chain_id
            or evidence[i].data_mode != position.data_mode for i in required_ids):
        return "QUOTE_UNAVAILABLE", None, external, "Quote or closing-plan evidence unavailable"
    if (q.chain_id != position.chain_id or not q.base_asset.same_asset(position.collateral_asset)
            or q.input_amount != position.collateral_amount):
        return "QUOTE_UNAVAILABLE", None, external, "Quote quantity, chain or collateral mismatch"
    if (q.status in BAD or q.observed_at > now or now - q.observed_at > timedelta(minutes=5)
            or q.valid_until is None or q.valid_until < now):
        return "QUOTE_UNAVAILABLE", None, external, "Quote stale or validity unknown"
    if context.route_available is False or context.closing_feasible is False:
        return "EXIT_NOT_FEASIBLE", None, external, "Closing route unavailable"
    if context.route_available is None or context.closing_feasible is None:
        return "INSUFFICIENT_DATA", None, external, "Closing feasibility unknown"
    if external and not context.repayment_liquidity_confirmed:
        return "EXIT_NOT_FEASIBLE", None, True, "Debt repayment requires external liquidity not confirmed"
    if not q.quote_asset.same_asset(position.loan_asset):
        return "QUOTE_UNAVAILABLE", None, external, "Conversion to loan asset is not covered by this quote"
    price = context.output_usd_price
    output_price = usd_price(price)
    if (output_price is None or not price.base_asset.same_asset(q.quote_asset)
            or price.chain_id != position.chain_id or price.observed_at > now
            or now - price.observed_at > timedelta(minutes=5)
            or not price.evidence_ids
            or any(i not in evidence or evidence[i].status in BAD or evidence[i].fetched_at > now or evidence[i].chain_id != position.chain_id
                   or evidence[i].data_mode != position.data_mode for i in price.evidence_ids)
            or context.gas_cost_usd is None or context.extra_fees_usd is None):
        return "INSUFFICIENT_DATA", None, external, "USD conversion, gas or additional fees missing/stale"
    valuation = value_position(snapshot, position, now=now)
    if valuation.collateral_value_usd is None or valuation.debt_value_usd is None:
        return "INSUFFICIENT_DATA", None, external, "Reference USD valuation unavailable"
    # Net quoted output already contains price impact and venue fees.
    net_proceeds = q.output_amount * output_price - context.gas_cost_usd - context.extra_fees_usd
    if net_proceeds < valuation.debt_value_usd:
        return "EXIT_NOT_FEASIBLE", None, external, "Proceeds do not cover debt; extra equity injection is outside MVP"
    return "OK", valuation.collateral_value_usd-net_proceeds, external, "Net quote output includes price impact; gas/extra fees added once"
