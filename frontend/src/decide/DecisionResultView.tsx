// STAY/EXIT result exactly as returned by POST /decisions; the browser never recomputes the verdict.
import type { DecisionRequest, DecisionResult } from '../api/types'
import { Kicker, ListRow, Tag } from '../components/ui'
import { fmtRatioPct, fmtUsd, fmtUsdSigned, num } from '../lib/format'
import { C, DECISION_STATUS, SCENARIOS, missingLabel } from '../lib/labels'
import { useRadar, type DecisionRecord, type SimRecord } from '../state/radar'
import { sensitivityBars } from './request'

function verdictText(d: DecisionResult): string {
  if (d.decision_status !== 'CONDITIONAL') return DECISION_STATUS[d.decision_status]?.verdict ?? d.decision_status
  if (d.comparison === 'EQUAL') return 'Tie'
  return d.verdict === 'STAY' ? 'Stay in the position' : d.verdict === 'EXIT' ? 'Exit the position' : 'No verdict'
}

function thresholdText(d: DecisionResult): string | null {
  const p = num(d.break_even_probability)
  if (d.break_even_status === 'WITHIN_FEASIBLE_RANGE' && p !== null) return `Exiting becomes preferable above ${fmtRatioPct(p, 2)} probability over ${d.horizon_days} days.`
  if (d.break_even_status === 'NO_REVERSAL_IN_FEASIBLE_RANGE') return 'No realistic probability flips the decision.'
  return null
}

export function DecisionResultView({ rec, stale, sims }: { rec: DecisionRecord; stale: boolean; sims: SimRecord[] }) {
  const r = useRadar()
  const d = rec.envelope.data
  const full = d.decision_status === 'CONDITIONAL' && d.stay_value_usd !== null && d.exit_value_usd !== null
  const mode = d.effective_mode === 'predictive' ? 'Predictive' : d.requested_mode === 'predictive' ? 'Normal (forecast unavailable)' : 'Normal'
  const simName = (id: string) => {
    const s = sims.find((x) => x.result.simulation_id === id)
    return s ? `${SCENARIOS[s.result.scenario_type].label} · ${s.targetName}` : id
  }

  const missing: { k: string; v: string }[] = []
  if (d.quote_status !== 'OK') missing.push({ k: 'Exit quote', v: d.quote_status })
  for (const f of d.carry_calculation?.missing_fields ?? []) missing.push({ k: missingLabel(f), v: 'Missing' })
  for (const f of d.closing_plan?.missing_fields ?? []) missing.push({ k: missingLabel(f), v: 'Missing' })
  for (const id of d.excluded_unquantified_scenario_ids) missing.push({ k: simName(id), v: 'Loss not quantified' })

  // Sensitivity drawn only when the linear projection reproduces the backend break-even.
  const sensRef = d.sensitivity_scenario_ref
  const sensSim = sims.find((s) => s.result.simulation_id === sensRef)
  const sensLoss = num(sensSim?.result.position_effects[sensSim.result.position_id]?.scenario_estimated_loss_usd)
  const sent = (JSON.parse(rec.key) as DecisionRequest).scenario_assumptions.find((a) => a.loss_scenario_ref === sensRef)
  const pNow = num(sent?.probability_over_horizon)
  const bars = full && sensLoss !== null && pNow !== null
    ? sensitivityBars(num(d.stay_value_usd)! - num(d.exit_value_usd)!, pNow, sensLoss, num(d.break_even_probability))
    : null
  const maxAbs = bars ? Math.max(...bars.map((b) => Math.abs(b.diff)), 1) : 1
  const threshold = full ? thresholdText(d) : null

  const card = (on: boolean) => ({
    display: 'flex', flexDirection: 'column' as const, gap: 8, padding: 18, borderRadius: 14,
    border: `1px solid ${on ? C.gold : 'rgba(163,180,202,0.2)'}`, background: on ? 'rgba(212,180,122,0.08)' : 'rgba(4,16,31,0.4)',
  })

  return (
    <>
      {stale && <div className="alert alert--warn">Assumptions changed: compare again.</div>}
      <section className="panel stack gap-16" aria-label="Result" style={{ padding: 22, opacity: stale ? 0.55 : 1 }}>
        <div className="row wrap between gap-8" style={{ alignItems: 'baseline', columnGap: 16 }}>
          <div className="stack gap-4">
            <Kicker gold>RESULT · {d.horizon_days} DAYS</Kicker>
            <span className="serif" style={{ fontSize: 30, lineHeight: 1.1, color: full ? C.text : DECISION_STATUS[d.decision_status]?.color }}>{verdictText(d)}</span>
          </div>
          <span className="mono" style={{ fontSize: 10, color: C.unk }}>{mode} mode</span>
        </div>

        {!full && (
          <>
            {missing.map((m) => (
              <div key={m.k} className="row between gap-12" style={{ fontSize: 12, padding: '8px 0', borderTop: '1px solid rgba(163,180,202,0.1)' }}>
                <span>{m.k}</span><span style={{ color: C.cau, textAlign: 'right' }}>{m.v}</span>
              </div>
            ))}
            <div className="row wrap gap-8">
              <button type="button" className="btn" onClick={() => r.setDrawer({ kind: 'data' })}>Data</button>
            </div>
          </>
        )}

        {full && (
          <>
            <div className="grid-auto" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
              <div style={card(d.verdict === 'STAY')}>
                <div className="row between" style={{ alignItems: 'baseline' }}><span className="serif" style={{ fontSize: 24 }}>Stay</span><Tag color={C.unk}>STAY</Tag></div>
                <span className="serif" style={{ fontSize: 30 }}>{fmtUsd(d.stay_value_usd)}</span>
                <ListRow k="Current equity" v={fmtUsd(d.equity_now_usd)} mono />
                <ListRow k={`${d.horizon_days}-day net carry`} v={fmtUsdSigned(d.net_carry_usd)} mono />
                <ListRow k="Expected loss" v={d.expected_loss_usd === null ? 'Unknown' : `−${fmtUsd(d.expected_loss_usd)}`} mono />
              </div>
              <div style={card(d.verdict === 'EXIT')}>
                <div className="row between" style={{ alignItems: 'baseline' }}><span className="serif" style={{ fontSize: 24 }}>Exit</span><Tag color={C.unk}>EXIT</Tag></div>
                <span className="serif" style={{ fontSize: 30 }}>{fmtUsd(d.exit_value_usd)}</span>
                <ListRow k="Current equity" v={fmtUsd(d.equity_now_usd)} mono />
                <ListRow k="Exit cost" v={d.exit_cost_usd === null ? 'Unknown' : `−${fmtUsd(d.exit_cost_usd)}`} mono />
                <ListRow k="Difference vs Stay" v={fmtUsdSigned(num(d.exit_value_usd)! - num(d.stay_value_usd)!)} mono />
              </div>
            </div>
            {threshold && <div style={{ fontSize: 13, color: C.textSoft }}>{threshold}</div>}
            {bars && (
              <div className="stack gap-8">
                <div className="row wrap between gap-10">
                  <Kicker>PROBABILITY SENSITIVITY</Kicker>
                  <span className="row gap-10" style={{ fontSize: 10, color: C.unk }}>
                    <span className="row gap-4"><span className="dot" style={{ background: C.gold }} />Stay</span>
                    <span className="row gap-4"><span className="dot" style={{ background: C.dan }} />Exit</span>
                  </span>
                </div>
                <div role="img" aria-label="Advantage of staying or exiting by probability"
                  style={{ display: 'grid', gridTemplateColumns: 'repeat(13, minmax(0, 1fr))', gap: 4, height: 120, borderTop: '1px solid rgba(163,180,202,0.1)', borderBottom: '1px solid rgba(163,180,202,0.1)' }}>
                  {bars.map((b) => {
                    const h = `${Math.min(100, (Math.abs(b.diff) / maxAbs) * 100)}%`
                    return (
                      <div key={b.p} title={`${fmtRatioPct(b.p, 1)} → ${fmtUsdSigned(b.diff)}`} style={{ height: '100%', display: 'grid', gridTemplateRows: '1fr 1fr' }}>
                        <div style={{ display: 'flex', alignItems: 'flex-end', borderBottom: '1px solid rgba(163,180,202,0.25)' }}>
                          <div style={{ width: '100%', height: b.diff > 0 ? h : 0, background: C.gold, borderRadius: '3px 3px 0 0' }} />
                        </div>
                        <div style={{ display: 'flex', alignItems: 'flex-start' }}>
                          <div style={{ width: '100%', height: b.diff < 0 ? h : 0, background: C.dan, borderRadius: '0 0 3px 3px' }} />
                        </div>
                      </div>
                    )
                  })}
                </div>
                <div className="mono" style={{ display: 'grid', gridTemplateColumns: 'repeat(13, minmax(0, 1fr))', gap: 4, fontSize: 8, color: C.unk, textAlign: 'center' }}>
                  {bars.map((b) => <span key={b.p}>{fmtRatioPct(b.p, bars[1].p < 0.01 - 1e-9 ? 1 : 0)}</span>)}
                </div>
              </div>
            )}
          </>
        )}

        <div className="stack gap-4">
          <Kicker>SCENARIOS</Kicker>
          {d.included_scenario_ids.map((id) => <ListRow key={id} k={simName(id)} v="Included" vColor={C.pos} />)}
          {d.excluded_scenario_ids.map((id) => <ListRow key={id} k={simName(id)} v="Excluded" vColor={C.unk} />)}
        </div>
        <div className="row wrap gap-8">
          <button type="button" className="btn" onClick={() => r.setDrawer({ kind: 'evidence', title: 'Decision sources', ids: [...new Set([...(d.carry_calculation?.evidence_ids ?? []), ...sims.filter((s) => d.included_scenario_ids.includes(s.result.simulation_id)).flatMap((s) => s.result.evidence_ids)])] })}>Evidence</button>
          <button type="button" className="btn" onClick={() => r.setTab('simulate')}>Edit scenario</button>
        </div>
      </section>
    </>
  )
}
