// Sized exit quote loaded by the engine for this exact position (catalog quote contexts).
import type { QuoteContext } from '../api/types'
import { Tag } from '../components/ui'
import { fmtAmount, fmtDateTime, fmtFixed, fmtRatioPct, num } from '../lib/format'
import { C } from '../lib/labels'
import type { PositionView } from '../lib/model'
import { useRadar } from '../state/radar'
import { quoteFor } from './request'

function quoteValid(q: QuoteContext, evaluatedAt: string | null): boolean {
  const now = evaluatedAt ? new Date(evaluatedAt).getTime() : Date.now()
  const until = q.quote.valid_until ? new Date(q.quote.valid_until).getTime() : null
  const age = (now - new Date(q.quote.observed_at).getTime()) / 1000
  return until !== null && now < until && age >= 0 && age < 300
}

export function QuotePanel({ sel }: { sel: PositionView }) {
  const r = useRadar()
  const q = quoteFor(sel.quotes, r.decision)
  const valid = q ? quoteValid(q, r.evaluatedAt) : false
  const badge = !q ? { label: 'UNAVAILABLE', color: C.unk } : valid ? { label: 'AVAILABLE', color: C.pos } : { label: 'EXPIRED', color: C.cau }
  const qty = num(sel.dto.collateral_amount)
  const input = num(q?.quote.input_amount)
  const coverage = qty && input ? input / qty : null
  const row = (k: string, v: string, color?: string) => (
    <><span style={{ color: C.unk }}>{k}</span><span className="mono" style={{ fontSize: 11, color }}>{v}</span></>
  )

  return (
    <div className="panel stack gap-12 pad-lg">
      <div className="row wrap between gap-10" style={{ alignItems: 'baseline' }}>
        <span className="h-panel">Sell {sel.ptName} → {sel.loanName}</span>
        <Tag color={badge.color}>{badge.label}</Tag>
      </div>
      {sel.quotes.length > 1 && (
        <select className="select" aria-label="Quote" value={q?.quote_id ?? ''} onChange={(e) => r.setDecision({ quoteId: e.target.value })}>
          {sel.quotes.map((x) => <option key={x.quote_id} value={x.quote_id}>{x.quote_id}</option>)}
        </select>
      )}
      {!q && <span style={{ fontSize: 12, color: C.sec }}>No quote for this position size.</span>}
      {q && (
        <div style={{ display: 'grid', gridTemplateColumns: 'auto minmax(0, 1fr)', gap: '7px 14px', fontSize: 12 }}>
          {row('Quantity sold', `${fmtAmount(q.quote.input_amount, sel.ptName, 2)}${coverage !== null && Math.abs(coverage - 1) > 1e-9 ? ` (${fmtRatioPct(coverage, 0)})` : ''}`)}
          {row('Amount received', fmtAmount(q.quote.output_amount, sel.loanName, 0))}
          {row('Execution price', `${fmtFixed(q.quote.value, 4)} ${sel.loanName}`)}
          {row('Price impact', q.quote.price_impact === null ? 'Unknown' : `−${fmtRatioPct(Math.abs(num(q.quote.price_impact)!), 2)}`)}
          {row('Route', q.quote.venue ?? '—')}
          {row('Valid until', fmtDateTime(q.quote.valid_until), valid ? undefined : C.cau)}
        </div>
      )}
      {q && (
        <button type="button" className="btn--link" style={{ alignSelf: 'flex-start', fontSize: 11 }}
          onClick={() => r.setDrawer({ kind: 'evidence', title: 'Quote sources', ids: [...new Set([...q.evidence_ids, ...q.quote.evidence_ids, ...(q.output_usd_price?.evidence_ids ?? [])])] })}>
          Quote sources
        </button>
      )}
    </div>
  )
}
