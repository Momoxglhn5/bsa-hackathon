import { useState } from 'react'
import { postCarry, postDecision } from '../api/endpoints'
import type { CarryResult, CoreEnvelope } from '../api/types'
import { ErrorBox, Kicker, ListRow, Seg, Spinner, Tag } from '../components/ui'
import { fmtRatioPct, fmtUsd, fmtUsdSigned, num } from '../lib/format'
import { C, SCENARIOS } from '../lib/labels'
import { useRadar, type CarryMode } from '../state/radar'
import { useLoad, useRequest } from '../state/useRequest'
import { ClosingPanel } from './ClosingPanel'
import { DecisionResultView } from './DecisionResultView'
import { QuotePanel } from './QuotePanel'
import { buildDecisionRequest, carryAssumptions, type DecisionErrors } from './request'

export function DecideTab() {
  const r = useRadar()
  const sel = r.sel!
  const d = r.decision
  const [errors, setErrors] = useState<DecisionErrors>({})
  const req = useRequest<void>()
  const mySims = r.sims.filter((s) => s.result.position_id === sel.id && s.sourceId === r.source.id && s.result.scenario_status !== 'NOT_APPLICABLE')
  const built = buildDecisionRequest(sel.strategyId, sel.id, d, mySims, sel.quotes)
  const res = r.decisionResult
  const stale = !!res && res.key !== JSON.stringify(built.body)

  const compare = async () => {
    setErrors(built.errors)
    if (!built.body) return
    const body = built.body
    const key = JSON.stringify(body)
    await req.run(async (signal) => {
      const envelope = await postDecision(r.source, body, { signal })
      r.setDecisionResult({ envelope, key, snapshotId: envelope.provenance.snapshot_id })
    })
  }

  return (
    <>
      <section className="panel row wrap gap-14" aria-label="Mode and horizon" style={{ padding: '14px 20px', columnGap: 28 }}>
        <div className="stack" style={{ flex: '1 1 220px', gap: 2 }}>
          <span className="serif" style={{ fontSize: 24 }}>Stay or exit</span>
          <span style={{ fontSize: 12, color: C.sec }}>{sel.title} · {sel.pair}</span>
        </div>
        <div className="stack gap-6"><Kicker>HORIZON</Kicker>
          <Seg label="Decision horizon" value={d.horizon} onChange={(h) => r.setDecision({ horizon: h })} options={[1, 3, 7].map((h) => ({ id: h, label: `${h}d` }))} />
        </div>
        <div className="stack gap-6"><Kicker>ANALYSIS MODE</Kicker>
          <Seg label="Analysis mode" value={d.mode} onChange={(m) => r.setDecision({ mode: m })} options={[{ id: 'normal', label: 'Normal' }, { id: 'predictive', label: 'Predictive' }]} />
        </div>
      </section>

      <div className="grid-auto" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 360px), 1fr))', alignItems: 'start' }}>
        <section className="panel stack gap-18 pad-lg" aria-label="Decision assumptions">
          <div className="stack gap-8">
            <Kicker>COMPARED SCENARIOS</Kicker>
            {mySims.length === 0 && (
              <div style={{ fontSize: 12, color: C.sec }}>
                No simulation for this position yet. <button type="button" className="btn--link" onClick={() => r.setTab('simulate')}>Run a simulation →</button>
              </div>
            )}
            {mySims.map((s) => {
              const id = s.result.simulation_id
              const own = s.result.position_effects[sel.id]?.scenario_estimated_loss_usd ?? null
              const prob = d.prob[id] ?? '5'
              const slider = Math.min(50, Math.max(0, num(prob.replace(',', '.')) ?? 0))
              const reusable = s.snapshotId === r.snapshotId
              return (
                <div key={id} className="subcard stack gap-8" style={{ opacity: reusable ? 1 : 0.5 }}>
                  <label className="row gap-10" style={{ fontSize: 12 }}>
                    <input type="checkbox" checked={!!d.picks[id]} disabled={!reusable}
                      onChange={(e) => r.setDecision({ picks: { ...d.picks, [id]: e.target.checked } })} />
                    <span className="grow">{SCENARIOS[s.result.scenario_type].label} · {s.targetName}</span>
                    <span className="mono" style={{ fontSize: 11, color: own === null ? C.unk : C.dan }}>{own === null ? 'loss not quantified' : `−${fmtUsd(own)}`}</span>
                  </label>
                  <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 72px', gap: 10, alignItems: 'center' }}>
                    <input type="range" min={0} max={50} step={0.5} value={slider} aria-label="Probability over the horizon"
                      onChange={(e) => r.setDecision({ prob: { ...d.prob, [id]: e.target.value } })} />
                    <input className="input" style={{ height: 30, textAlign: 'right', fontSize: 12 }} value={prob} inputMode="decimal" aria-label="Probability in %"
                      aria-invalid={!!errors[`prob:${id}`]} onChange={(e) => r.setDecision({ prob: { ...d.prob, [id]: e.target.value } })} />
                  </div>
                  <span className="note">Probability over {d.horizon} days (%)</span>
                  {errors[`prob:${id}`] && <span className="field-error">{errors[`prob:${id}`]}</span>}
                  {s.result.horizon_days !== d.horizon && <span style={{ fontSize: 10, color: C.cau }}>Simulated over {s.result.horizon_days}d, decision over {d.horizon}d.</span>}
                  {own === null && d.picks[id] && (
                    <label className="row gap-8" style={{ fontSize: 11, color: C.cau }}>
                      <input type="checkbox" checked={!!d.excludeUnquantified[id]} onChange={(e) => r.setDecision({ excludeUnquantified: { ...d.excludeUnquantified, [id]: e.target.checked } })} />
                      Exclude from comparison
                    </label>
                  )}
                </div>
              )
            })}
            {errors.scenarios && <span className="field-error">{errors.scenarios}</span>}
          </div>
          {Object.values(d.picks).filter(Boolean).length > 1 && (
            <div className="stack gap-6">
              <Kicker>SCENARIOS</Kicker>
              <Seg label="Scenario exclusivity" value={d.exclusivity} onChange={(v) => r.setDecision({ exclusivity: v })}
                options={[{ id: 'EXCLUSIVE', label: 'Exclusive' }, { id: 'OVERLAP', label: 'Can overlap' }, { id: 'UNKNOWN', label: 'Unknown' }]} />
              <label className="field-label" style={{ fontSize: 11 }}>Reference scenario for the break-even
                <select className="select" value={d.sensitivityRef} onChange={(e) => r.setDecision({ sensitivityRef: e.target.value })}>
                  <option value="">First scenario</option>
                  {mySims.filter((s) => d.picks[s.result.simulation_id]).map((s) => <option key={s.result.simulation_id} value={s.result.simulation_id}>{SCENARIOS[s.result.scenario_type].label} · {s.targetName}</option>)}
                </select>
              </label>
            </div>
          )}
          <CarrySection key={`carry-${sel.id}`} errors={errors} />
        </section>

        <section className="stack gap-14" aria-label="Exit">
          <QuotePanel key={`q-${sel.id}`} sel={sel} />
          <ClosingPanel key={`c-${sel.id}`} sel={sel} />
          <button type="button" className="btn btn--primary btn--lg" onClick={compare} disabled={req.loading}>{req.loading ? 'Comparing…' : 'Compare STAY / EXIT'}</button>
          {Object.keys(errors).length > 0 && !errors.scenarios && <span className="field-error">Fix the highlighted fields.</span>}
          {req.loading && <Spinner label="Comparing…" />}
          {req.error && <ErrorBox message={req.error} onRetry={compare} />}
        </section>
      </div>

      {res && <DecisionResultView rec={res} stale={stale} sims={mySims} />}
    </>
  )
}

function CarrySection({ errors }: { errors: DecisionErrors }) {
  const r = useRadar()
  const sel = r.sel!
  const d = r.decision
  const [localErr, setLocalErr] = useState<DecisionErrors>({})
  const engine = useLoad<CoreEnvelope<CarryResult>>(d.carryMode === 'engine' ? `${r.source.id}|${sel.id}|${d.horizon}` : null,
    (signal) => postCarry(r.source, { strategy_id: sel.strategyId, position_id: sel.id, horizon_days: d.horizon }, { signal }))
  const rates = useRequest<CoreEnvelope<CarryResult>>()
  const err = { ...errors, ...localErr }
  const previewRates = async () => {
    const e: DecisionErrors = {}
    const assumptions = carryAssumptions(d, e)
    setLocalErr(e)
    if (!assumptions) return
    await rates.run((signal) => postCarry(r.source, { strategy_id: sel.strategyId, position_id: sel.id, horizon_days: d.horizon, assumptions }, { signal }))
  }
  const c = d.carryMode === 'engine' ? engine.data?.data : d.carryMode === 'rates' ? rates.data?.data : null
  const input = (k: 'carryUsd' | 'carryWhy' | 'ptReturnPct' | 'borrowApyPct' | 'holdingFeesUsd' | 'ratesWhy', label: string, placeholder?: string, mode: 'decimal' | 'text' = 'decimal') => (
    <label className="field-label">
      {label}
      <input className="input" value={d[k]} inputMode={mode === 'decimal' ? 'decimal' : undefined} placeholder={placeholder} aria-invalid={!!err[k]}
        onChange={(e) => r.setDecision({ [k]: e.target.value } as never)} />
      {err[k] && <span className="field-error">{err[k]}</span>}
    </label>
  )
  return (
    <div className="stack gap-8">
      <Kicker>NET CARRY OVER THE HORIZON</Kicker>
      <Seg<CarryMode> label="Net carry source" value={d.carryMode} onChange={(v) => { r.setDecision({ carryMode: v }); rates.reset() }}
        options={[{ id: 'engine', label: 'Market rates' }, { id: 'rates', label: 'Custom rates' }, { id: 'manual', label: 'Custom amount' }]} />
      {d.carryMode === 'rates' && (
        <>
          <div className="grid-auto" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 10 }}>
            {input('ptReturnPct', 'PT return (%)', 'e.g. 8')}
            {input('borrowApyPct', 'Borrow rate (%)', 'e.g. 5')}
            {input('holdingFeesUsd', 'Fees (USD)')}
          </div>
          {input('ratesWhy', 'Rate source', 'e.g. internal consensus', 'text')}
          <button type="button" className="btn" style={{ alignSelf: 'flex-start' }} onClick={previewRates} disabled={rates.loading}>{rates.loading ? 'Computing…' : 'Compute'}</button>
        </>
      )}
      {d.carryMode === 'manual' && (
        <>
          {input('carryUsd', 'Estimated net gain (USD)', 'e.g. 1000')}
          {input('carryWhy', 'Source', 'e.g. team estimate', 'text')}
        </>
      )}
      {engine.loading && d.carryMode === 'engine' && <Spinner label="Computing…" />}
      {engine.error && d.carryMode === 'engine' && <ErrorBox message={engine.error} />}
      {rates.error && <ErrorBox message={rates.error} onRetry={previewRates} />}
      {c && (
        <div className="subcard stack gap-4">
          <div className="row between gap-10"><Kicker>{c.horizon_days} DAYS</Kicker><Tag color={c.net_carry_usd === null ? C.cau : C.pos}>{c.net_carry_usd === null ? 'UNAVAILABLE' : 'CALCULATED'}</Tag></div>
          <ListRow k="Collateral return" v={fmtUsdSigned(c.collateral_return_usd)} unknown={c.collateral_return_usd === null} mono />
          <ListRow k="Debt cost" v={c.borrowing_cost_usd === null ? 'Unavailable' : fmtUsdSigned(-(num(c.borrowing_cost_usd) ?? 0))} unknown={c.borrowing_cost_usd === null} mono />
          <ListRow k="Net carry" v={fmtUsdSigned(c.net_carry_usd)} unknown={c.net_carry_usd === null} mono vColor={C.gold} />
          <ListRow k="Rates (PT / borrow)" v={`${fmtRatioPct(c.pt_annual_return, 2)} / ${fmtRatioPct(c.borrow_apy, 2)}`} mono />
        </div>
      )}
    </div>
  )
}
