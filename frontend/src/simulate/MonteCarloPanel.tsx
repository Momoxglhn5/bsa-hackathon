// Monte Carlo on the oracle price (POST /monte-carlo). Response is nested: envelope.data is a MC result.
import { useState } from 'react'
import { postMonteCarlo } from '../api/endpoints'
import type { CoreEnvelope, MonteCarloRequest, MonteCarloResult } from '../api/types'
import { ErrorBox, Kicker, ListRow, Metric, Seg, Spinner, StatusBadge } from '../components/ui'
import { fmtAmount, fmtFixed, fmtRatioPct, num, percentTextToRatio } from '../lib/format'
import { C, missingLabel } from '../lib/labels'
import { useRadar } from '../state/radar'
import { useRequest } from '../state/useRequest'

interface McForm {
  horizon: 1 | 3 | 7
  paths: string
  seed: string
  vol: string
  drift: string
  debt: 'FROZEN_DEBT' | 'CONSTANT_APR'
  apr: string
}

const DEFAULT: McForm = { horizon: 7, paths: '10000', seed: '42', vol: '25', drift: '0', debt: 'FROZEN_DEBT', apr: '' }

export function MonteCarloPanel() {
  const r = useRadar()
  const sel = r.sel!
  const [f, setF] = useState<McForm>(DEFAULT)
  const [errs, setErrs] = useState<Record<string, string>>({})
  const req = useRequest<{ env: CoreEnvelope<MonteCarloResult>; key: string }>()
  const set = (o: Partial<McForm>) => setF((x) => ({ ...x, ...o }))
  const key = JSON.stringify({ f, p: sel.id, s: r.source.id })
  const stale = !!req.data && req.data.key !== key

  const run = async () => {
    const e: Record<string, string> = {}
    const paths = Number(f.paths)
    if (!Number.isInteger(paths) || paths < 1000 || paths > 50000) e.paths = 'Between 1,000 and 50,000.'
    const seed = Number(f.seed)
    if (!Number.isInteger(seed) || seed < 0) e.seed = 'Positive integer.'
    const body: MonteCarloRequest = {
      strategy_id: sel.strategyId, position_id: sel.id, horizon_days: f.horizon, num_paths: paths, seed,
      volatility_source: 'USER_ASSUMPTION', debt_model: f.debt,
    }
    const v = percentTextToRatio(f.vol)
    if (v === null || Number(v) < 0 || Number(v) > 5) e.vol = 'Between 0 and 500%.'
    else body.annual_volatility = v
    const d = percentTextToRatio(f.drift || '0')
    if (d === null) e.drift = 'Percentage expected.'
    else body.annual_drift = d
    if (f.debt === 'CONSTANT_APR') {
      const a = percentTextToRatio(f.apr)
      if (a === null || Number(a) < 0) e.apr = 'APR required.'
      else body.borrow_apr = a
    }
    setErrs(e)
    if (Object.keys(e).length) return
    await req.run(async (signal) => ({ env: await postMonteCarlo(r.source, body, { signal }), key }))
  }

  const mc = req.data?.env.data
  const d = mc?.data
  return (
    <div className="grid-auto" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 360px), 1fr))', alignItems: 'start' }}>
      <section className="panel stack gap-18 pad-lg" aria-label="Monte Carlo parameters">
        <div className="stack gap-4">
          <Kicker gold>STATISTICAL SIMULATION</Kicker>
          <span className="h-section">Oracle price Monte Carlo</span>
        </div>
        <div className="grid-auto" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
          <div className="stack gap-6"><Kicker>HORIZON</Kicker>
            <Seg label="Horizon" value={f.horizon} onChange={(h) => set({ horizon: h })} options={[1, 3, 7].map((h) => ({ id: h as 1 | 3 | 7, label: `${h}d` }))} />
          </div>
          <div className="stack gap-6"><Kicker>DEBT</Kicker>
            <Seg label="Debt model" value={f.debt} onChange={(v) => set({ debt: v })} options={[{ id: 'FROZEN_DEBT', label: 'Frozen' }, { id: 'CONSTANT_APR', label: 'Constant APR' }]} />
          </div>
        </div>
        <div className="inset stack gap-12">
          <Kicker>PARAMETERS</Kicker>
          <div className="grid-auto" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 10 }}>
            <McField label="Annual volatility (%)" value={f.vol} onChange={(v) => set({ vol: v })} error={errs.vol} />
            <McField label="Annual drift (%)" value={f.drift} onChange={(v) => set({ drift: v })} error={errs.drift} />
            {f.debt === 'CONSTANT_APR' && <McField label="Borrow APR (%)" value={f.apr} onChange={(v) => set({ apr: v })} error={errs.apr} placeholder={fmtFixed((num(sel.morpho?.morpho?.borrow_apy) ?? 0) * 100, 2)} />}
            <McField label="Paths" value={f.paths} onChange={(v) => set({ paths: v })} error={errs.paths} inputMode="numeric" />
            <McField label="Seed" value={f.seed} onChange={(v) => set({ seed: v })} error={errs.seed} inputMode="numeric" />
          </div>
        </div>
        <div className="row wrap gap-8">
          <button type="button" className="btn btn--primary" onClick={run} disabled={req.loading}>{req.loading ? 'Simulating…' : 'Run Monte Carlo'}</button>
          <button type="button" className="btn" style={{ height: 38 }} onClick={() => { setF(DEFAULT); req.reset(); setErrs({}) }}>Reset</button>
        </div>
        {req.error && <ErrorBox message={req.error} onRetry={run} />}
      </section>

      <section className="stack gap-14" aria-label="Monte Carlo result">
        {!mc && !req.loading && (
          <div className="panel stack gap-8" style={{ padding: 28 }}>
            <span className="serif" style={{ fontSize: 24 }}>No result yet</span>
            <span style={{ fontSize: 12, color: C.sec }}>Run the simulation to estimate the LTV distribution at the horizon.</span>
          </div>
        )}
        {req.loading && <div className="panel" style={{ padding: 28 }}><Spinner label="Simulating…" /></div>}
        {mc && !req.loading && (
          <>
            {stale && <div className="alert alert--warn">Parameters changed: run the simulation again.</div>}
            <div className="panel stack gap-16" style={{ padding: 22, opacity: stale ? 0.55 : 1 }}>
              <div className="row wrap between gap-8">
                <div className="stack gap-4">
                  <Kicker gold>RESULT · {d?.horizon_days ?? f.horizon} DAYS · {d?.total_paths ?? f.paths} PATHS</Kicker>
                  <span className="serif" style={{ fontSize: 24 }}>{sel.title}</span>
                </div>
                <StatusBadge status={mc.status} />
              </div>
              {!d && (
                <div className="stack gap-6">
                  <span style={{ fontSize: 13, color: C.textSoft }}>Simulation unavailable for this position.</span>
                  {mc.missing_fields.map((m) => <ListRow key={m} k={missingLabel(m)} v="Missing" vColor={C.cau} />)}
                </div>
              )}
              {d && (
                <>
                  <div className="grid-auto" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 8 }}>
                    <Metric k="Threshold breach" v={fmtRatioPct(d.simulated_threshold_breach_frequency, 2)} unknown={d.simulated_threshold_breach_frequency === null}
                      sub={`${d.breached_paths ?? '—'} / ${d.total_paths ?? '—'} paths`} />
                    <Metric k="Critical oracle drop" v={fmtRatioPct(d.critical_oracle_drop_ratio, 1)} unknown={d.critical_oracle_drop_ratio === null} />
                    <Metric k="VaR 95%" v={fmtAmount(d.oracle_mark_to_model_var95_loan_units, '', 0)} unknown={d.oracle_mark_to_model_var95_loan_units === null} sub={sel.loanName} />
                    <Metric k="ES 95%" v={fmtAmount(d.oracle_mark_to_model_es95_loan_units, '', 0)} unknown={d.oracle_mark_to_model_es95_loan_units === null} sub={sel.loanName} />
                  </div>
                  <LtvGauge initial={num(d.initial_ltv)} p50={num(d.terminal_ltv_p50)} p95={num(d.terminal_ltv_p95)} lltv={num(r.loaded.risks[sel.strategyId]?.data.position_risk[sel.id]?.lltv_ratio)} />
                  <div className="stack">
                    <ListRow k="Initial LTV" v={fmtRatioPct(d.initial_ltv, 2)} mono />
                    <ListRow k="Horizon LTV (median / p95)" v={`${fmtRatioPct(d.terminal_ltv_p50, 2)} / ${fmtRatioPct(d.terminal_ltv_p95, 2)}`} mono />
                    <ListRow k="Health factor initial / p05 minimum" v={`${fmtFixed(d.initial_health_factor, 3)} / ${fmtFixed(d.min_health_factor_p05, 3)}`} mono />
                    <ListRow k="95% confidence interval" v={`${fmtRatioPct(d.mc_sampling_ci_95?.low, 2)} – ${fmtRatioPct(d.mc_sampling_ci_95?.high, 2)}`} mono />
                  </div>
                </>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  )
}

function McField({ label, value, onChange, error, placeholder, inputMode = 'decimal' }: { label: string; value: string; onChange: (v: string) => void; error?: string; placeholder?: string; inputMode?: 'decimal' | 'numeric' }) {
  return (
    <label className="field-label">
      {label}
      <input className="input" value={value} onChange={(e) => onChange(e.target.value)} inputMode={inputMode} placeholder={placeholder} aria-invalid={!!error} />
      {error && <span className="field-error">{error}</span>}
    </label>
  )
}

/** Places the returned LTV quantiles on one axis next to LLTV; no invented distribution. */
function LtvGauge({ initial, p50, p95, lltv }: { initial: number | null; p50: number | null; p95: number | null; lltv: number | null }) {
  const vals = [initial, p50, p95, lltv].filter((v): v is number => v !== null)
  if (vals.length < 2) return null
  const lo = Math.max(0, Math.min(...vals) - 0.05)
  const hi = Math.max(...vals) + 0.05
  const x = (v: number) => `${((v - lo) / (hi - lo)) * 100}%`
  const marks = [
    { v: initial, label: 'Initial', color: C.sec },
    { v: p50, label: 'Median', color: C.gold },
    { v: p95, label: 'p95', color: C.cau },
    { v: lltv, label: 'LLTV', color: C.dan },
  ].filter((m) => m.v !== null) as { v: number; label: string; color: string }[]
  return (
    <div className="stack gap-10" aria-label="Initial LTV, horizon quantiles and liquidation threshold">
      <Kicker>LTV AT HORIZON</Kicker>
      <div style={{ position: 'relative', height: 18 }}>
        <div style={{ position: 'absolute', left: 0, right: 0, top: 8, height: 2, background: 'rgba(163,180,202,0.18)', borderRadius: 2 }} />
        {lltv !== null && <div style={{ position: 'absolute', left: x(lltv), right: 0, top: 8, height: 2, background: 'rgba(241,124,130,0.35)' }} />}
        {marks.map((m) => (
          <span key={m.label} style={{ position: 'absolute', left: x(m.v), top: 0, width: 2, height: 18, transform: 'translateX(-50%)', background: m.color, borderRadius: 1 }} />
        ))}
      </div>
      <div className="row wrap gap-16" style={{ fontSize: 11 }}>
        {marks.map((m) => (
          <span key={m.label} className="row gap-6">
            <span className="dot" style={{ background: m.color }} />
            <span style={{ color: C.sec }}>{m.label}</span>
            <span className="mono" style={{ color: C.text }}>{fmtRatioPct(m.v, 1)}</span>
          </span>
        ))}
      </div>
    </div>
  )
}
