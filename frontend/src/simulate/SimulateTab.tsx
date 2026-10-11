import { useMemo, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import { postSimulation } from '../api/endpoints'
import type { GraphDTO, ScenarioParameters, ScenarioScope, ScenarioType, SimulationRequest } from '../api/types'
import { ErrorBox, Kicker, Metric, Seg, Spinner, StatusBadge, Tag } from '../components/ui'
import { decimalText, fmtFixed, fmtRatioPct, fmtUsd, num, percentTextToRatio } from '../lib/format'
import { C, LINK_STATUS, RELATION_LABEL, SCENARIOS } from '../lib/labels'
import { edgeBetween, nodeNames, reach, type PositionView } from '../lib/model'
import { useRadar, type SimForm, type SimRecord } from '../state/radar'
import { useRequest } from '../state/useRequest'
import { MonteCarloPanel } from './MonteCarloPanel'

const SCOPES: { id: ScenarioScope; label: string }[] = [
  { id: 'VERIFIED_TARGET', label: 'Verified' },
  { id: 'OBSERVED_TARGET', label: 'Observed' },
  { id: 'HYPOTHETICAL', label: 'Hypothetical' },
]

function targetsFor(graph: GraphDTO | null, positionId: string, type: ScenarioType): string[] {
  if (!graph) return []
  const R = reach(graph, positionId)
  const types = SCENARIOS[type].targetTypes
  return [...R.keys()].filter((k) => k !== positionId && types.includes(graph.nodes.find((n) => n.id === k)!.type))
    .sort((a, b) => R.get(a)! - R.get(b)!)
}

function defaultTarget(sel: PositionView, type: ScenarioType, targets: string[]): string {
  const m = sel.morpho?.morpho
  const own = type.startsWith('ORACLE') && m ? `oracle:${sel.dto.chain_id}:${m.oracle_address.toLowerCase()}`
    : type === 'UNAUTHORIZED_MINTING' ? `asset:${sel.dto.chain_id}:${sel.dto.collateral_asset.address.toLowerCase()}` : ''
  return targets.includes(own) ? own : targets[0] ?? ''
}

function scopeAllowed(graph: GraphDTO | null, target: string, scope: ScenarioScope): boolean {
  if (scope === 'HYPOTHETICAL' || !graph) return true
  const incoming = graph.edges.filter((e) => e.target === target)
  return scope === 'VERIFIED_TARGET' ? incoming.some((e) => e.verification_status === 'CONFIRMED')
    : incoming.some((e) => e.verification_status === 'CONFIRMED' || e.verification_status === 'OBSERVED')
}

type Errors = Partial<Record<'mintQty' | 'backing' | 'haircut' | 'ccHours' | 'ccHaircut', string>>

function buildParameters(f: SimForm): { params: ScenarioParameters; errors: Errors } {
  const errors: Errors = {}
  const params: ScenarioParameters = {}
  const pct = (text: string, key: keyof Errors, set: (v: string) => void) => {
    if (!text.trim()) return
    const n = num(text.replace(',', '.'))
    const ratio = percentTextToRatio(text)
    if (ratio === null || n === null || n < 0 || n > 100) errors[key] = 'Between 0 and 100%.'
    else set(ratio)
  }
  switch (f.type) {
    case 'ORACLE_MANIPULATION':
      params.oracle_price_change_ratio = percentTextToRatio(String(f.oracle)) ?? '0'
      break
    case 'UNAUTHORIZED_MINTING':
      if (f.mintQty.trim()) {
        const v = decimalText(f.mintQty)
        if (v === null || Number(v) < 0) errors.mintQty = 'Positive quantity.'
        else params.mint_amount = v
      }
      pct(f.backing, 'backing', (v) => { params.backing_impairment_ratio = v })
      pct(f.haircut, 'haircut', (v) => { params.assumed_token_haircut_ratio = v })
      break
    case 'CROSS_CHAIN_FAILURE': {
      const h = decimalText(f.ccHours)
      if (f.ccHours.trim() && (h === null || Number(h) < 0 || Number(h) > 8760)) errors.ccHours = 'Between 0 and 8,760 hours.'
      else if (h !== null) params.outage_duration_hours = h
      params.unavailable_service = f.ccService
      pct(f.ccHaircut, 'ccHaircut', (v) => { params.assumed_token_haircut_ratio = v; params.haircut_applies_to_affected_collateral = f.ccApply })
      break
    }
    case 'ORACLE_OUTAGE':
      break
  }
  return { params, errors }
}

export function SimulateTab() {
  const [mode, setMode] = useState<'stress' | 'mc'>('stress')
  return (
    <>
      <div className="row wrap gap-10" style={{ padding: '0 4px' }}>
        <Seg label="Simulation type" value={mode} onChange={setMode}
          options={[{ id: 'stress', label: 'Dependency shock' }, { id: 'mc', label: 'Monte Carlo' }]} />
      </div>
      {mode === 'stress' ? <StressSimulator /> : <MonteCarloPanel />}
    </>
  )
}

function StressSimulator() {
  const r = useRadar()
  const sel = r.sel!
  const graph = r.graphFor(sel.strategyId)
  const names = useMemo(() => (graph ? nodeNames(graph, r.positions, r.loaded.catalog?.data ?? null) : new Map()), [graph, r.positions, r.loaded.catalog])
  const f = r.simForm
  const scenarioTypes = (r.loaded.scenarios.length ? r.loaded.scenarios : (Object.keys(SCENARIOS) as ScenarioType[]))
  const targets = targetsFor(graph, sel.id, f.type)
  const target = targets.includes(f.target) ? f.target : defaultTarget(sel, f.type, targets)
  const req = useRequest<SimRecord>()
  const [errors, setErrors] = useState<Errors>({})
  const formRef = useRef<HTMLElement>(null)
  const formKey = JSON.stringify({ ...f, target, position: sel.id, source: r.source.id })
  const sim = r.lastSim && r.lastSim.result.position_id === sel.id ? r.lastSim : null
  const stale = !!sim && r.lastSimFormKey !== formKey

  const run = async () => {
    if (!target) return
    const { params, errors: errs } = buildParameters(f)
    setErrors(errs)
    if (Object.keys(errs).length) return
    const body: SimulationRequest = {
      strategy_id: sel.strategyId, position_id: sel.id, scenario_type: f.type, target_node_id: target,
      parameters: params, horizon_days: f.horizon, scenario_scope: f.scope,
    }
    const rec = await req.run(async (signal) => {
      const env = await postSimulation(r.source, body, { signal })
      return {
        result: env.data, request: body, snapshotId: env.provenance.snapshot_id, sourceId: r.source.id,
        envelopeStatus: env.status, envelopeWarnings: env.warnings, targetName: names.get(target)?.name ?? target, ranAt: Date.now(),
      }
    })
    if (rec) r.recordSim(rec, formKey)
  }

  const fieldErrors = req.apiError instanceof ApiError ? req.apiError.fieldErrors() : []
  const fieldMsg = (key: string) => fieldErrors.find((e) => e.loc.includes(key))?.msg

  return (
    <div className="grid-auto" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 360px), 1fr))', alignItems: 'start' }}>
      <section ref={formRef} className="panel stack gap-18 pad-lg" aria-label="Shock parameters">
        <div className="stack gap-4">
          <Kicker gold>STRESS SIMULATOR</Kicker>
          <span className="h-section">Configure a shock</span>
        </div>
        <div className="stack gap-8">
          <Kicker>SCENARIO TYPE</Kicker>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 6 }}>
            {scenarioTypes.map((t) => {
              const available = targetsFor(graph, sel.id, t).length > 0
              return (
                <button key={t} type="button" aria-pressed={f.type === t} disabled={!available}
                  title={available ? undefined : 'No dependency of this type for this position'}
                  onClick={() => { const tg = targetsFor(graph, sel.id, t); r.setSimForm({ type: t, target: defaultTarget(sel, t, tg) }) }}
                  style={{
                    display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 3, padding: '10px 12px', borderRadius: 12,
                    border: `1px solid ${f.type === t ? C.gold : 'rgba(163,180,202,0.2)'}`, background: f.type === t ? 'rgba(212,180,122,0.1)' : 'rgba(4,16,31,0.4)',
                    color: C.text, cursor: available ? 'pointer' : 'not-allowed', textAlign: 'left', opacity: available ? 1 : 0.4,
                  }}>
                  <span style={{ fontSize: 12 }}>{SCENARIOS[t].label}</span>
                  <span style={{ fontSize: 10, color: C.unk, lineHeight: 1.4 }}>{SCENARIOS[t].desc}</span>
                </button>
              )
            })}
          </div>
        </div>
        <label className="field-label">
          <Kicker>TARGET</Kicker>
          <select className="select" value={target} disabled={!targets.length} onChange={(e) => r.setSimForm({ target: e.target.value })}>
            {targets.map((t) => <option key={t} value={t}>{names.get(t)?.name ?? t}</option>)}
            {!targets.length && <option value="">No target</option>}
          </select>
        </label>
        <div className="grid-auto" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
          <div className="stack gap-6">
            <Kicker>HORIZON</Kicker>
            <Seg label="Horizon" value={f.horizon} onChange={(h) => r.setSimForm({ horizon: h })} options={[1, 3, 7, 30].map((h) => ({ id: h, label: `${h}d` }))} />
          </div>
          <div className="stack gap-6">
            <Kicker>TARGET EVIDENCE</Kicker>
            <Seg label="Target evidence" value={f.scope} onChange={(s) => r.setSimForm({ scope: s })}
              options={SCOPES.map((s) => ({ ...s, disabled: !scopeAllowed(graph, target, s.id) }))} />
          </div>
        </div>
        <div className="inset stack gap-12">
          <Kicker>ASSUMPTIONS</Kicker>
          {f.type === 'ORACLE_MANIPULATION' && (
            <label className="field-label" style={{ gap: 8 }}>
              <span className="row between"><span>Oracle price change</span><span className="mono" style={{ color: C.gold }}>{f.oracle > 0 ? '+' : ''}{f.oracle}%</span></span>
              <input type="range" min={-90} max={50} step={1} value={f.oracle} onChange={(e) => r.setSimForm({ oracle: Number(e.target.value) })} aria-label="Oracle price change in percent" />
            </label>
          )}
          {f.type === 'ORACLE_OUTAGE' && <span style={{ fontSize: 12, color: C.sec }}>The oracle stops responding.</span>}
          {f.type === 'UNAUTHORIZED_MINTING' && (
            <>
              <Field label="Collateral haircut (%)" value={f.haircut} onChange={(v) => r.setSimForm({ haircut: v })} placeholder="e.g. 20" error={errors.haircut ?? fieldMsg('assumed_token_haircut_ratio')} />
              <Field label="Minted quantity (units)" value={f.mintQty} onChange={(v) => r.setSimForm({ mintQty: v })} placeholder="Optional" error={errors.mintQty ?? fieldMsg('mint_amount')} />
              <Field label="Backing impairment (%)" value={f.backing} onChange={(v) => r.setSimForm({ backing: v })} placeholder="Optional" error={errors.backing ?? fieldMsg('backing_impairment_ratio')} />
            </>
          )}
          {f.type === 'CROSS_CHAIN_FAILURE' && (
            <>
              <Field label="Outage duration (hours)" value={f.ccHours} onChange={(v) => r.setSimForm({ ccHours: v })} inputMode="numeric" error={errors.ccHours ?? fieldMsg('outage_duration_hours')} />
              <div className="field-label">Unavailable service
                <Seg label="Service" value={f.ccService} onChange={(v) => r.setSimForm({ ccService: v })} options={[{ id: 'redemption', label: 'Redemption' }, { id: 'bridge_transfer', label: 'Bridge transfer' }]} />
              </div>
              <Field label="Haircut (%)" value={f.ccHaircut} onChange={(v) => r.setSimForm({ ccHaircut: v })} placeholder="Optional" error={errors.ccHaircut} />
              <label className="row gap-8" style={{ fontSize: 12 }}>
                <input type="checkbox" checked={f.ccApply} onChange={(e) => r.setSimForm({ ccApply: e.target.checked })} />
                Apply the haircut to collateral
              </label>
            </>
          )}
        </div>
        <div className="row wrap gap-8">
          <button type="button" className="btn btn--primary" onClick={run} disabled={req.loading || !target}>{req.loading ? 'Computing…' : 'Run simulation'}</button>
          <button type="button" className="btn" style={{ height: 38 }} onClick={() => { r.resetSimForm(); req.reset(); setErrors({}) }}>Reset</button>
        </div>
        {req.error && !fieldErrors.length && <ErrorBox message={req.error} onRetry={run} />}
        {fieldErrors.length > 0 && <div className="field-error">{req.error}</div>}
      </section>

      <section aria-label="Impacts" className="stack gap-14">
        {!sim && !req.loading && (
          <div className="panel stack gap-8" style={{ padding: 28 }}>
            <span className="serif" style={{ fontSize: 24 }}>No result yet</span>
            <span style={{ fontSize: 12, color: C.sec }}>Pick a scenario and a target, then run the simulation.</span>
          </div>
        )}
        {req.loading && <div className="panel" style={{ padding: 28 }}><Spinner label="Computing…" /></div>}
        {sim && !req.loading && <StressResult rec={sim} stale={stale} onEdit={() => formRef.current?.querySelector('button')?.focus()} />}
      </section>
    </div>
  )
}

function Field({ label, value, onChange, placeholder, error, inputMode = 'decimal' }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; error?: string; inputMode?: 'decimal' | 'numeric'
}) {
  return (
    <label className="field-label">
      {label}
      <input className="input" value={value} onChange={(e) => onChange(e.target.value)} inputMode={inputMode} placeholder={placeholder} aria-invalid={!!error} />
      {error && <span className="field-error">{error}</span>}
    </label>
  )
}

function StressResult({ rec, stale, onEdit }: { rec: SimRecord; stale: boolean; onEdit: () => void }) {
  const r = useRadar()
  const sel = r.sel!
  const res = rec.result
  const graph = r.graphFor(res.strategy_id)
  const names = useMemo(() => (graph ? nodeNames(graph, r.positions, r.loaded.catalog?.data ?? null) : new Map()), [graph, r.positions, r.loaded.catalog])
  const eff = res.position_effects[sel.id]
  const ownLoss = eff?.scenario_estimated_loss_usd ?? null
  const na = res.scenario_status === 'NOT_APPLICABLE'
  const path = res.representative_paths[sel.id] ?? []
  const nm = (id: string) => names.get(id)?.short ?? r.positions.find((p) => p.id === id)?.title ?? id
  const reusable = rec.snapshotId === r.snapshotId
  const liq = (v: boolean | null) => (v ? 'Liquidatable' : v === false ? 'No' : 'Unknown')
  const rows = eff ? [
    { k: 'Collateral (market price)', b: fmtUsd(eff.before.collateral_value_usd), a: fmtUsd(eff.after.collateral_value_usd) },
    { k: 'Equity', b: fmtUsd(eff.before.equity_usd), a: fmtUsd(eff.after.equity_usd) },
    { k: 'LTV (oracle)', b: fmtRatioPct(eff.before.ltv_ratio), a: fmtRatioPct(eff.after.ltv_ratio) },
    { k: 'Health factor', b: fmtFixed(eff.before.health_factor), a: fmtFixed(eff.after.health_factor) },
    { k: 'Liquidation', b: liq(eff.before.liquidatable), a: liq(eff.after.liquidatable) },
  ] : []
  return (
    <>
      {stale && <div className="alert alert--warn">Assumptions changed: run the simulation again.</div>}
      <div className="panel stack gap-16" style={{ padding: 22, opacity: stale ? 0.55 : 1 }}>
        <div className="row wrap between gap-8" style={{ alignItems: 'flex-start' }}>
          <div className="stack gap-4">
            <Kicker gold>RESULT · {res.horizon_days} DAYS</Kicker>
            <span className="serif" style={{ fontSize: 24, lineHeight: 1.1 }}>{SCENARIOS[res.scenario_type].label} · {rec.targetName}</span>
          </div>
          <StatusBadge status={res.scenario_status} />
        </div>
        {na && <div style={{ fontSize: 13, color: C.textSoft }}>Scenario not applicable to this position.</div>}
        {!na && (
          <>
            <div className="grid-auto" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 8 }}>
              <Metric k="Estimated loss" v={ownLoss === null ? 'Not quantifiable' : fmtUsd(ownLoss)} unknown={ownLoss === null} color={ownLoss !== null ? C.dan : undefined} />
              <Metric k="Potential exposure" v={fmtUsd(res.potentially_exposed_capital_usd)} unknown={res.potentially_exposed_capital_usd === null} />
              <Metric k="Blocked capital" v={res.operationally_blocked_usd === null ? '—' : fmtUsd(res.operationally_blocked_usd)} unknown={res.operationally_blocked_usd === null}
                color={num(res.operationally_blocked_usd) ? C.cau : undefined} />
              <Metric k="Affected positions" v={String(res.affected_position_ids.length)} />
            </div>
            {eff && (
              <div className="stack">
                <div className="ba-head kicker kicker--sm"><span>{sel.title.toUpperCase()}</span><span>BEFORE</span><span>AFTER</span></div>
                {rows.map((x) => (
                  <div key={x.k} className="ba-row">
                    <span style={{ color: C.sec }}>{x.k}</span>
                    <span className="mono">{x.b}</span>
                    <span className="mono" style={{ color: x.a === 'Liquidatable' ? C.dan : x.a !== x.b ? C.cau : C.text }}>{x.a}</span>
                  </div>
                ))}
              </div>
            )}
            <div className="stack gap-6">
              <Kicker>AFFECTED POSITIONS</Kicker>
              {res.affected_position_ids.map((id) => {
                const p = r.positions.find((x) => x.id === id)
                const loss = res.position_effects[id]?.scenario_estimated_loss_usd ?? null
                return (
                  <div key={id} className="list-row">
                    <span style={{ color: C.textSoft }}>{p ? `${p.title} · ${p.pair}` : id}</span>
                    <span className="mono" style={{ fontSize: 11, color: loss ? C.dan : C.unk }}>{loss === null ? '—' : `−${fmtUsd(loss)}`}</span>
                  </div>
                )
              })}
            </div>
            {path.length > 1 && (
              <div className="stack gap-2">
                <Kicker>PROPAGATION PATH</Kicker>
                {path.slice(1).map((id, i) => {
                  const e = graph ? edgeBetween(graph, path[i], id) : undefined
                  const st = e ? LINK_STATUS[e.verification_status] : null
                  return (
                    <div key={id + i} className="row gap-10" style={{ fontSize: 11, padding: '5px 0' }}>
                      <span className="mono" style={{ fontSize: 9, color: C.gold, width: 18 }}>{String(i + 1).padStart(2, '0')}</span>
                      <span className="grow" style={{ color: C.textSoft }}>{nm(path[i])} → {nm(id)}{e ? ` · ${RELATION_LABEL[e.relation_type] ?? e.relation_type}` : ''}</span>
                      {st && <Tag color={st.color}>{st.short}</Tag>}
                    </div>
                  )
                })}
              </div>
            )}
          </>
        )}
        <div className="row wrap gap-8" style={{ paddingTop: 4 }}>
          <button type="button" className="btn" onClick={onEdit}>Edit assumptions</button>
          <button type="button" className="btn" disabled={path.length < 2}
            onClick={() => { r.setPgAll(false); r.showPath(path); r.setPgNode(rec.request.target_node_id); r.setPage('graph') }}>Show impact path</button>
          <button type="button" className="btn" onClick={() => r.setDrawer({ kind: 'evidence', title: 'Simulation sources', ids: res.evidence_ids })}>Evidence</button>
          {!na && reusable && (
            <button type="button" className="btn btn--primary" style={{ height: 34 }}
              onClick={() => {
                r.setDecision({ picks: { ...r.decision.picks, [res.simulation_id]: true }, horizon: [1, 3, 7].includes(res.horizon_days) ? res.horizon_days : r.decision.horizon })
                r.setDecisionResult(null)
                r.setTab('decide')
              }}>Use in STAY/EXIT →</button>
          )}
        </div>
      </div>
    </>
  )
}
