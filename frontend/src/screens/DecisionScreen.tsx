import { useState } from 'react'
import { usePositionInputs } from '../data/usePositionInputs'
import { useRadar } from '../data/useRadar'
import { amount, pct } from '../lib/format'
import { decide, healthFactor, simulateStress } from '../lib/scenarios'
import type { Verdict } from '../lib/scenarios'
import { Card, EstimateBadge, Facts, Notes, SectionLabel, Segmented, StatusBadge } from '../ui/primitives'
import PriceField from '../ui/PriceField'

const VERDICT_LOOK: Record<Verdict, { color: string; border: string; bg: string; size: string }> = {
  STAY: { color: '#34d399', border: '#065f46', bg: '#111d22', size: 'text-[44px]' },
  EXIT: { color: '#f87171', border: '#7f1d1d', bg: '#1f1620', size: 'text-[44px]' },
  INSUFFICIENT_DATA: { color: '#cbd5e1', border: '#475569', bg: '#141c2e', size: 'text-[26px]' },
}

/** Percent input bound to a fraction; empty text = null. */
function PercentInput({ id, label, value, onChange, hint }: { id: string; label: string; value: number | null; onChange: (v: number | null) => void; hint?: string }) {
  const [text, setText] = useState(value === null ? '' : String(+(value * 100).toFixed(4)))
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs text-ink-2">
        {label}
      </label>
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="number"
          inputMode="decimal"
          min="0"
          step="0.01"
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            const v = Number(e.target.value)
            onChange(e.target.value === '' || !Number.isFinite(v) || v < 0 ? null : v / 100)
          }}
          className="h-8 w-28 rounded-md border border-[#243049] bg-control px-2.5 font-mono text-ink"
        />
        <span className="text-ink-3">%</span>
      </div>
      {hint && <span className="text-[10.5px] leading-snug text-ink-4">{hint}</span>}
    </div>
  )
}

export default function DecisionScreen() {
  const { position, graph } = useRadar()
  const model = usePositionInputs()
  const [horizon, setHorizon] = useState(3)
  const [oracleRate, setOracleRate] = useState<number | null>(0.004)
  const [backingRate, setBackingRate] = useState<number | null>(0.003)
  const [oracleLgd, setOracleLgd] = useState<number | null>(0.18)
  const [backingLgd, setBackingLgd] = useState<number | null>(0.12)
  const [exitCost, setExitCost] = useState<number | null>(null)

  const sym = model.loanSymbol
  const p = model.inputs
  const exposure = p ? p.collateralAmount * p.collateralPrice : 0
  const assumptionsComplete = oracleRate !== null && backingRate !== null && oracleLgd !== null && backingLgd !== null
  const result =
    p && assumptionsComplete
      ? decide({
          exposure,
          horizonDays: horizon,
          oracleRatePerDay: oracleRate,
          backingRatePerDay: backingRate,
          oracleLgd,
          backingLgd,
          exitCostShare: exitCost,
          gasCost: 0,
        })
      : null
  const verdict: Verdict = result?.verdict ?? 'INSUFFICIENT_DATA'
  const look = VERDICT_LOOK[verdict]
  const baseline = p ? simulateStress(p, { scenario: 'oracle', shock: 0, exitHaircut: 0, oracleTracksBacking: false }) : null
  const unresolved = graph.edges.filter((e) => e.status === 'UNRESOLVED').length

  let why: string
  if (!p) why = model.problem ?? 'Position inputs are missing.'
  else if (!assumptionsComplete) why = 'An incident-rate or loss assumption is empty: no verdict.'
  else if (exitCost === null) why = 'No exit cost: the API has no exit-quote route yet. Enter the exit cost you can get (spread + price impact) to compare.'
  else if (verdict === 'EXIT') why = `Over ${horizon} day(s) the expected loss of staying (${amount(result!.expectedLoss, sym)}) exceeds the cost of exiting now (${amount(result!.exitCost, sym)}).`
  else why = `Over ${horizon} day(s) exiting now (${amount(result!.exitCost, sym)}) costs more than the expected loss of staying (${amount(result!.expectedLoss, sym)}).`

  const notes = [
    ...(position?.data.kind === 'SIMULATED' ? ['The position is SIMULATED (GET /positions/{id}), not read from a wallet.'] : []),
    'Incident rates and losses are your assumptions: GET /incident-frequencies is not implemented.',
    ...(unresolved ? [`${unresolved} relation(s) in the graph are UNRESOLVED: the backing loss is an assumption.`] : []),
    ...(model.priceSource === 'market' ? ['Collateral is valued at its last market price: the Morpho oracle price is not served.'] : []),
    ...(position?.warnings ?? []),
  ]

  return (
    <div className="grid flex-1 grid-cols-1 items-start gap-4 p-4 md:grid-cols-2 xl:grid-cols-3">
      <div className="flex flex-col gap-4">
        <Card label="Position overview" className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-2">
            <SectionLabel>Position</SectionLabel>
            {position && <StatusBadge status={position.status} />}
          </div>
          {p && baseline ? (
            <Facts
              rows={[
                { label: 'Collateral', value: amount(p.collateralAmount, model.collateralSymbol, 2) },
                { label: 'Collateral value', value: amount(exposure, sym) },
                { label: 'Debt', value: amount(p.debt, sym, 2) },
                { label: 'LTV / LLTV', value: `${pct(baseline.ltvBefore, 1)} / ${pct(p.lltv, 1)}` },
                { label: 'Health factor', value: healthFactor(p, exposure).toFixed(3) },
                { label: 'Oracle drop to HF = 1', value: pct(baseline.liquidationDistance, 1) },
              ]}
            />
          ) : (
            <p className="m-0 text-xs text-ink-3">{model.problem}</p>
          )}
          <PriceField model={model} />
        </Card>
        <Card label="Data quality" className="flex flex-col gap-3">
          <SectionLabel>Data quality</SectionLabel>
          <Notes items={notes} />
        </Card>
      </div>

      <Card label="Assumptions" className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-2">
          <SectionLabel>Assumptions</SectionLabel>
          <EstimateBadge text="EDITABLE" />
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-xs text-ink-2">Horizon</span>
          <Segmented<number> label="Horizon" value={horizon} onChange={setHorizon} options={[1, 3, 7, 30].map((d) => ({ value: d, label: `${d} d` }))} />
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <PercentInput id="or" label="Oracle incident rate / day" value={oracleRate} onChange={setOracleRate} />
          <PercentInput id="ol" label="Loss if oracle incident" value={oracleLgd} onChange={setOracleLgd} />
          <PercentInput id="br" label="Backing incident rate / day" value={backingRate} onChange={setBackingRate} />
          <PercentInput id="bl" label="Loss if backing incident" value={backingLgd} onChange={setBackingLgd} />
        </div>
        <PercentInput
          id="exit"
          label="Exit cost now (spread + price impact)"
          value={exitCost}
          onChange={setExitCost}
          hint="Empty = no executable quote → INSUFFICIENT_DATA. POST /exit-quotes is not implemented."
        />
        {result && (
          <Facts
            rows={[
              { label: `P(incident in ${horizon} d)`, value: pct(result.incidentProbability) },
              { label: 'Expected loss if STAY', value: amount(result.expectedLoss, sym) },
              { label: 'Exit cost', value: result.exitCost === null ? 'Unavailable' : amount(result.exitCost, sym), missing: result.exitCost === null },
            ]}
          />
        )}
      </Card>

      <section
        aria-labelledby="verdict-title"
        className="flex flex-col gap-3 rounded-lg border p-5 md:col-span-2 xl:col-span-1"
        style={{ borderColor: look.border, background: look.bg }}
      >
        <div className="flex items-center justify-between gap-2">
          <SectionLabel id="verdict-title">Decision</SectionLabel>
          <EstimateBadge text="INDICATIVE" />
        </div>
        <div className={`font-mono font-medium tracking-wide ${look.size}`} style={{ color: look.color }} aria-live="polite">
          {verdict}
        </div>
        <p className="m-0 text-[13px] leading-relaxed text-[#e2e8f0]">{why}</p>
        <div className="rounded-md bg-[rgba(11,17,32,.55)] px-3 py-2.5 text-[11.5px] leading-normal text-ink-3">
          Rule: EXIT when the expected loss of staying exceeds the cost of exiting now. Break-even horizon:{' '}
          <span className="font-mono text-ink">{result?.breakEvenDays ? `${result.breakEvenDays.toFixed(1)} days` : 'n/a'}</span>.
        </div>
        <p className="m-0 text-[11px] text-ink-4">Computed in the browser from the inputs on this page: POST /decisions is not implemented.</p>
      </section>
    </div>
  )
}
