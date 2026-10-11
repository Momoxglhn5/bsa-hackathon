// Builds STAY/EXIT, carry and closing requests from the form. Validation mirrors the
// backend contract so errors appear next to the field; the backend stays authoritative.
import type { CarryAssumptions, ClosingAssumptions, DecisionRequest, QuoteContext } from '../api/types'
import { decimalText, num, percentTextToRatio } from '../lib/format'
import type { DecisionForm, SimRecord } from '../state/radar'

export type DecisionErrors = Partial<Record<string, string>>

export function quoteFor(quotes: QuoteContext[], form: DecisionForm): QuoteContext | null {
  return quotes.find((q) => q.quote_id === form.quoteId) ?? quotes[0] ?? null
}

export function carryAssumptions(f: DecisionForm, errors: DecisionErrors): CarryAssumptions | null {
  const pt = percentTextToRatio(f.ptReturnPct)
  const borrow = percentTextToRatio(f.borrowApyPct)
  const fees = decimalText(f.holdingFeesUsd || '0')
  if (pt === null) errors.ptReturnPct = 'Annual PT return in %.'
  if (borrow === null) errors.borrowApyPct = 'Borrow APY in %.'
  if (fees === null || Number(fees) < 0) errors.holdingFeesUsd = 'USD amount, zero or more.'
  if (f.ratesWhy.trim().length < 10) errors.ratesWhy = 'Source required (10 characters minimum).'
  if (pt === null || borrow === null || fees === null || f.ratesWhy.trim().length < 10) return null
  return { pt_annual_return: pt, borrow_apy: borrow, holding_fees_usd: fees, explanation: f.ratesWhy.trim() }
}

export function closingAssumptions(f: DecisionForm, errors: DecisionErrors): ClosingAssumptions | null {
  const funds = decimalText(f.closingFunds)
  const gas = decimalText(f.closingGas)
  const extra = decimalText(f.closingExtra)
  const fin = decimalText(f.closingFinancing)
  if (funds === null || Number(funds) < 0) errors.closingFunds = 'Available amount in loan token units.'
  if (gas === null || Number(gas) < 0) errors.closingGas = 'Total gas in USD (0 accepted).'
  if (extra === null || Number(extra) < 0) errors.closingExtra = 'Fees in USD (0 accepted).'
  if (fin === null || Number(fin) < 0) errors.closingFinancing = 'Financing fees in USD (0 accepted).'
  if (f.closingWhy.trim().length < 10) errors.closingWhy = 'Source required (10 characters minimum).'
  if ([funds, gas, extra, fin].some((v) => v === null) || f.closingWhy.trim().length < 10) return null
  return {
    available_repayment_loan_units: funds!, gas_cost_usd: gas!, extra_fees_usd: extra!, financing_fee_usd: fin!,
    basis: 'USER_ASSUMPTION', explanation: f.closingWhy.trim(),
  }
}

export function buildDecisionRequest(
  strategyId: string, positionId: string, f: DecisionForm, sims: SimRecord[], quotes: QuoteContext[],
): { body: DecisionRequest | null; errors: DecisionErrors } {
  const errors: DecisionErrors = {}
  const picked = sims.filter((s) => f.picks[s.result.simulation_id])
  if (!picked.length) errors.scenarios = 'Select at least one simulation for this position.'
  const assumptions = picked.map((s) => {
    const id = s.result.simulation_id
    const text = f.prob[id] ?? '5'
    let probability: string | null = null
    if (text.trim() !== '') {
      const ratio = percentTextToRatio(text)
      const n = num(ratio)
      if (ratio === null || n === null || n < 0 || n > 1) errors[`prob:${id}`] = 'Probability between 0 and 100%.'
      else probability = ratio
    }
    return {
      loss_scenario_ref: id, probability_over_horizon: probability, basis: 'USER_ASSUMPTION' as const,
      include_in_financial_comparison: !f.excludeUnquantified[id],
    }
  })
  const exclusive = f.exclusivity === 'EXCLUSIVE'
  if (exclusive) {
    const total = assumptions.filter((a) => a.include_in_financial_comparison).reduce((acc, a) => acc + (num(a.probability_over_horizon) ?? 0), 0)
    if (total > 1 + 1e-12) errors.scenarios = 'Exclusive scenarios: probabilities must sum to 100% or less.'
  }
  const body: DecisionRequest = {
    strategy_id: strategyId, position_id: positionId, mode: f.mode, horizon_days: f.horizon,
    scenario_assumptions: assumptions, scenarios_mutually_exclusive: exclusive,
    exit_quote_id: quoteFor(quotes, f)?.quote_id ?? null,
  }
  const included = assumptions.filter((a) => a.include_in_financial_comparison).map((a) => a.loss_scenario_ref)
  if (included.length > 1) body.sensitivity_scenario_ref = included.includes(f.sensitivityRef) ? f.sensitivityRef : included[0]
  if (f.carryMode === 'manual') {
    const v = decimalText(f.carryUsd)
    if (v === null) errors.carryUsd = 'USD amount expected (negative accepted).'
    if (!f.carryWhy.trim()) errors.carryWhy = 'Source required.'
    if (v !== null && f.carryWhy.trim()) {
      body.estimated_net_carry_usd = v
      body.carry_explanation = f.carryWhy.trim()
    }
  } else if (f.carryMode === 'rates') {
    body.carry_assumptions = carryAssumptions(f, errors)
  }
  if (f.useClosing) body.closing_assumptions = closingAssumptions(f, errors)
  return { body: Object.keys(errors).length ? null : body, errors }
}

/**
 * Linear projection of STAY − EXIT along the sensitivity scenario probability, derived only
 * from backend outputs; it is drawn only if it reproduces the backend break-even.
 */
export function sensitivityBars(stayMinusExit: number, pNow: number, loss: number, breakEven: number | null): { p: number; diff: number }[] | null {
  if (!(loss > 0) || breakEven === null) return null
  const derived = pNow + stayMinusExit / loss
  if (Math.abs(derived - breakEven) > Math.max(1e-9, Math.abs(breakEven) * 1e-6)) return null
  // Smallest round step whose 12 intervals reach twice the break-even, capped at 100 %.
  const step = [0.0025, 0.005, 0.01, 0.025, 0.05, 0.1].find((s) => 12 * s >= 2 * Math.max(breakEven, 0)) ?? 1 / 12
  const top = Math.min(1, 12 * step)
  return Array.from({ length: 13 }, (_, i) => {
    const p = (top * i) / 12
    return { p, diff: stayMinusExit - (p - pNow) * loss }
  })
}
