"""Read-only repay/withdraw/sell plan under explicit external-funding assumptions."""
from typing import Literal
from decimal import localcontext
from pydantic import Field

from app.models.evidence import ContractModel, NonNegativeDecimal, PreciseDecimal


class ClosingAssumptions(ContractModel):
    available_repayment_loan_units: NonNegativeDecimal = Field(le=10**60)
    gas_cost_usd: NonNegativeDecimal = Field(le=10**18)
    extra_fees_usd: NonNegativeDecimal = Field(le=10**18)
    financing_fee_usd: NonNegativeDecimal = Field(le=10**18)
    basis: Literal["USER_ASSUMPTION"] = "USER_ASSUMPTION"
    explanation: str = Field(min_length=10, max_length=1000)


class ClosingPlanResult(ContractModel):
    status: str
    position_id: str
    quote_id: str | None
    basis: str
    required_repayment_loan_units: NonNegativeDecimal | None
    available_repayment_loan_units: NonNegativeDecimal | None = None
    repayment_shortfall_loan_units: NonNegativeDecimal | None = None
    gas_cost_usd: NonNegativeDecimal | None = None
    extra_fees_usd: NonNegativeDecimal | None = None
    financing_fee_usd: NonNegativeDecimal | None = None
    sale_proceeds_usd: NonNegativeDecimal | None = None
    net_proceeds_usd: PreciseDecimal | None = None
    total_operation_cost_usd: NonNegativeDecimal | None = None
    exit_cost_usd: PreciseDecimal | None = None
    net_equity_after_exit_usd: PreciseDecimal | None = None
    steps: tuple[str, ...] = (
        "Provide external loan-token funds to repay the current debt",
        "Repay Morpho debt before withdrawing collateral",
        "Withdraw the full PT collateral",
        "Sell the exact PT quantity using the quoted route",
        "Recover the funding principal and deduct financing, gas and additional fees",
    )
    missing_fields: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def assumed_context(context, position, assumptions):
    """Never promote a missing/failed swap route or call an assumed plan verified."""
    funded = position.borrow_amount is not None and assumptions.available_repayment_loan_units >= position.borrow_amount
    return context.model_copy(update={
        "closing_feasible": (funded if context.route_available is True and context.closing_feasible is not False
                             else context.closing_feasible),
        "requires_external_repayment_liquidity": True,
        "repayment_liquidity_confirmed": funded,
        "gas_cost_usd": assumptions.gas_cost_usd,
        "extra_fees_usd": assumptions.extra_fees_usd + assumptions.financing_fee_usd,
        "basis": "USER_ASSUMPTION",
    })


def closing_plan(snapshot, strategy_id, position_id, quote_id, now, assumptions=None):
    from app.analytics.valuation import value_position
    from app.decision.exit_cost import evaluate_exit
    position = snapshot.position(strategy_id, position_id)
    context = next((q for q in snapshot.quotes if q.quote_id == quote_id and q.position_id == position_id), None)
    status, cost, _, note = evaluate_exit(snapshot, position, quote_id, now, assumptions=assumptions)
    warnings = [note, "READ_ONLY_PLAN_NO_TRANSACTION_EXECUTED",
                "CURRENT_DEBT_AND_QUOTE_CAN_CHANGE_BEFORE_EXECUTION"]
    if assumptions:
        warnings.append("EXTERNAL_FUNDING_AND_COMPLETE_CLOSING_COSTS_ARE_USER_ASSUMPTIONS")
    missing = []
    if context is None:
        missing.append("execution_quote")
    elif assumptions is None:
        missing += [name for name in ("closing_feasible", "gas_cost_usd", "extra_fees_usd")
                    if getattr(context, name) is None]
        if context.requires_external_repayment_liquidity and not context.repayment_liquidity_confirmed:
            missing.append("repayment_liquidity_confirmed")
    elif position.borrow_amount is None or assumptions.available_repayment_loan_units < position.borrow_amount:
        missing.append("sufficient_external_repayment_funds")
    if status == "QUOTE_UNAVAILABLE" and context is not None:
        missing.append("fresh_exact_amount_quote_and_evidence")
    if status == "INSUFFICIENT_DATA":
        missing.append("complete_valuation_and_closing_inputs")
    valuation = value_position(snapshot, position, now=now)
    equity = valuation.equity_usd
    sale_proceeds = net_proceeds = operation_cost = remaining = None
    if cost is not None and equity is not None:
        effective = assumed_context(context, position, assumptions) if assumptions else context
        with localcontext() as ctx:
            ctx.prec = 60
            operation_cost = effective.gas_cost_usd + effective.extra_fees_usd
            net_proceeds = valuation.collateral_value_usd-cost
            sale_proceeds = net_proceeds+operation_cost
            remaining = equity-cost
    return ClosingPlanResult(status="CONDITIONAL" if status == "OK" else status,
        position_id=position_id, quote_id=quote_id,
        basis="USER_ASSUMPTION" if assumptions else context.basis if context else "UNAVAILABLE",
        required_repayment_loan_units=position.borrow_amount,
        available_repayment_loan_units=assumptions.available_repayment_loan_units if assumptions else None,
        repayment_shortfall_loan_units=max(0, position.borrow_amount-assumptions.available_repayment_loan_units)
            if assumptions is not None and position.borrow_amount is not None else None,
        gas_cost_usd=assumptions.gas_cost_usd if assumptions else context.gas_cost_usd if context else None,
        extra_fees_usd=assumptions.extra_fees_usd if assumptions else context.extra_fees_usd if context else None,
        financing_fee_usd=assumptions.financing_fee_usd if assumptions else None,
        sale_proceeds_usd=sale_proceeds, net_proceeds_usd=net_proceeds,
        total_operation_cost_usd=operation_cost,
        exit_cost_usd=cost, net_equity_after_exit_usd=remaining,
        missing_fields=tuple(missing), warnings=tuple(warnings))
