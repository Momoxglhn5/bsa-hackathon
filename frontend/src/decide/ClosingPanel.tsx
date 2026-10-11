// Read-only closing plan (POST /closing-plans): repay → withdraw → sell → recover funding.
import { useState } from 'react'
import { postClosingPlan } from '../api/endpoints'
import type { ClosingAssumptions, ClosingPlanResult, CoreEnvelope } from '../api/types'
import { ErrorBox, Kicker, ListRow, Spinner, Tag } from '../components/ui'
import { fmtAmount, fmtUsd, num } from '../lib/format'
import { C, missingLabel } from '../lib/labels'
import type { PositionView } from '../lib/model'
import { useRadar } from '../state/radar'
import { useLoad } from '../state/useRequest'
import { closingAssumptions, quoteFor, type DecisionErrors } from './request'

const STATUS: Record<string, { label: string; color: string }> = {
  CONDITIONAL: { label: 'FEASIBLE', color: C.pos },
  EXIT_NOT_FEASIBLE: { label: 'NOT FEASIBLE', color: C.dan },
  QUOTE_UNAVAILABLE: { label: 'QUOTE UNAVAILABLE', color: C.cau },
  INSUFFICIENT_DATA: { label: 'INCOMPLETE', color: C.cau },
}

export function ClosingPanel({ sel }: { sel: PositionView }) {
  const r = useRadar()
  const d = r.decision
  const q = quoteFor(sel.quotes, d)
  const [errors, setErrors] = useState<DecisionErrors>({})
  const [applied, setApplied] = useState<ClosingAssumptions | null>(null)
  const key = JSON.stringify([r.source.id, sel.id, q?.quote_id ?? null, applied])
  const req = useLoad<CoreEnvelope<ClosingPlanResult>>(key, (signal) =>
    postClosingPlan(r.source, { strategy_id: sel.strategyId, position_id: sel.id, exit_quote_id: q?.quote_id ?? null, assumptions: applied }, { signal }))
  const plan = req.data?.data

  const recompute = () => {
    const errs: DecisionErrors = {}
    const assumptions = d.useClosing ? closingAssumptions(d, errs) : null
    setErrors(errs)
    if (!Object.keys(errs).length) setApplied(assumptions)
  }

  const status = plan ? STATUS[plan.status] ?? { label: plan.status, color: C.cau } : null
  const field = (k: 'closingFunds' | 'closingGas' | 'closingExtra' | 'closingFinancing', label: string, hint?: string) => (
    <label className="field-label">
      {label}
      <input className="input" inputMode="decimal" value={d[k]} onChange={(e) => r.setDecision({ [k]: e.target.value } as never)} aria-invalid={!!errors[k]} placeholder={hint} />
      {errors[k] && <span className="field-error">{errors[k]}</span>}
    </label>
  )
  const shortfall = num(plan?.repayment_shortfall_loan_units)

  return (
    <div className="panel stack gap-10 pad-lg">
      <div className="row between gap-10" style={{ alignItems: 'baseline' }}>
        <span className="h-panel">Closing plan</span>
        {status && <Tag color={status.color}>{status.label}</Tag>}
      </div>
      {req.loading && <Spinner label="Computing the plan…" />}
      {req.error && <ErrorBox message={req.error} />}
      {plan && !req.loading && (
        <div className="stack gap-10">
          <div className="stack gap-2">
            <Kicker>REPAYMENT</Kicker>
            <ListRow k="Debt to repay" v={fmtAmount(plan.required_repayment_loan_units, sel.loanName, 0)} unknown={plan.required_repayment_loan_units === null} mono />
            {plan.available_repayment_loan_units !== null && <ListRow k="Available funding" v={fmtAmount(plan.available_repayment_loan_units, sel.loanName, 0)} mono />}
            {shortfall !== null && shortfall > 0 && <ListRow k="Funding shortfall" v={fmtAmount(plan.repayment_shortfall_loan_units, sel.loanName, 0)} mono vColor={C.dan} />}
          </div>
          <div className="stack gap-2">
            <Kicker>PT SALE</Kicker>
            <ListRow k="Sale proceeds" v={fmtUsd(plan.sale_proceeds_usd)} unknown={plan.sale_proceeds_usd === null} mono />
            <ListRow k="Net proceeds" v={fmtUsd(plan.net_proceeds_usd)} unknown={plan.net_proceeds_usd === null} mono />
          </div>
          <div className="stack gap-2">
            <Kicker>COSTS</Kicker>
            <ListRow k="Gas and fees" v={fmtUsd(plan.total_operation_cost_usd)} unknown={plan.total_operation_cost_usd === null} mono />
            <ListRow k="Total exit cost" v={fmtUsd(plan.exit_cost_usd)} unknown={plan.exit_cost_usd === null} mono vColor={C.cau} />
          </div>
          <div className="stack gap-2">
            <Kicker>RESULT</Kicker>
            <ListRow k="Equity after exit" v={fmtUsd(plan.net_equity_after_exit_usd)} unknown={plan.net_equity_after_exit_usd === null} mono vColor={C.gold} />
          </div>
          {plan.missing_fields.length > 0 && (
            <div className="stack gap-2">
              <Kicker>MISSING</Kicker>
              {plan.missing_fields.map((m) => <ListRow key={m} k={missingLabel(m)} v="—" vColor={C.cau} />)}
            </div>
          )}
        </div>
      )}
      <details open={d.useClosing}>
        <summary style={{ cursor: 'pointer', fontSize: 12, color: C.sec }}>Adjust closing assumptions</summary>
        <div className="inset stack gap-10" style={{ marginTop: 10 }}>
          <label className="row gap-8" style={{ fontSize: 12 }}>
            <input type="checkbox" checked={d.useClosing} onChange={(e) => r.setDecision({ useClosing: e.target.checked })} />
            Use my assumptions (also in the comparison)
          </label>
          <div className="grid-auto" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10, opacity: d.useClosing ? 1 : 0.5 }}>
            {field('closingFunds', `Funding (${sel.loanName})`, sel.dto.borrow_amount ? fmtAmount(sel.dto.borrow_amount, '', 0) : undefined)}
            {field('closingGas', 'Gas (USD)', 'e.g. 300')}
            {field('closingExtra', 'Fees (USD)')}
            {field('closingFinancing', 'Financing fees (USD)')}
          </div>
          <label className="field-label" style={{ opacity: d.useClosing ? 1 : 0.5 }}>Source
            <input className="input" value={d.closingWhy} onChange={(e) => r.setDecision({ closingWhy: e.target.value })} aria-invalid={!!errors.closingWhy} placeholder="e.g. team credit line" />
            {errors.closingWhy && <span className="field-error">{errors.closingWhy}</span>}
          </label>
          <button type="button" className="btn" style={{ alignSelf: 'flex-start' }} onClick={recompute} disabled={req.loading}>Recompute plan</button>
        </div>
      </details>
    </div>
  )
}
