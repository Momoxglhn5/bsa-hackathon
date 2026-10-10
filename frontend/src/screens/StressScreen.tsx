import { useMemo, useState } from 'react'
import { usePositionInputs } from '../data/usePositionInputs'
import { useRadar } from '../data/useRadar'
import PathFlow, { STEP_COLOR } from '../graph/PathFlow'
import type { StepState } from '../graph/PathFlow'
import { amount, pct, signed } from '../lib/format'
import { propagationPath } from '../lib/graph'
import type { Graph, PathStep } from '../lib/graph'
import { liquidationIncentive, simulateStress } from '../lib/scenarios'
import type { ScenarioId, StressResult } from '../lib/scenarios'
import { Card, EstimateBadge, Notes, SectionLabel, Segmented, VerificationBadge } from '../ui/primitives'
import PriceField from '../ui/PriceField'

interface Scenario {
  id: ScenarioId
  title: string
  description: string
  /** Graph node the shock starts from, found in the relations; null = not applicable. */
  target: (g: Graph) => string | null
  shockLabel: string
}

const SCENARIOS: Scenario[] = [
  {
    id: 'oracle',
    title: 'Oracle fault',
    description: 'The Morpho oracle under-prices the collateral. Tradable value is unchanged.',
    target: (g) => g.edges.find((e) => e.type === 'MARKET_ORACLE')?.target ?? null,
    shockLabel: 'Oracle price shock',
  },
  {
    id: 'backing',
    title: 'Backing failure',
    description: 'The asset backing the underlying loses value. Starts from the ECONOMIC_BACKING target.',
    target: (g) => g.edges.find((e) => e.type === 'ECONOMIC_BACKING')?.target ?? null,
    shockLabel: 'Backing value shock',
  },
  {
    id: 'crosschain',
    title: 'Cross-chain failure',
    description: 'Bridge or remote-chain outage. The data contract has no cross-chain relation type.',
    target: () => null,
    shockLabel: '',
  },
]

function stepStates(path: PathStep[], scenario: ScenarioId, r: StressResult, oracleTracks: boolean): StepState[] {
  return path.map((s, i) => {
    if (i === 0) return 'shocked'
    if (s.node.kind === 'market' && scenario === 'backing' && !oracleTracks) return 'unchanged'
    if (s.node.kind === 'market' || s.node.kind === 'position') return r.liquidatable ? 'shocked' : r.estimatedLoss > 0 || r.hfAfter < r.hfBefore ? 'impaired' : 'unchanged'
    return 'impaired'
  })
}

export default function StressScreen() {
  const { graph } = useRadar()
  const model = usePositionInputs()
  const [scenario, setScenario] = useState<ScenarioId>('oracle')
  const [shockPct, setShockPct] = useState(12)
  const [haircutPct, setHaircutPct] = useState(2)
  const [oracleTracks, setOracleTracks] = useState(false)

  const spec = SCENARIOS.find((s) => s.id === scenario)!
  const positionNode = graph.nodes.find((n) => n.kind === 'position')?.id ?? null
  const target = spec.target(graph)
  const path = useMemo(
    () => (target && positionNode ? propagationPath(graph, target, positionNode) : null),
    [graph, target, positionNode],
  )
  const applicable = path !== null
  const result = model.inputs
    ? simulateStress(model.inputs, {
        scenario: applicable ? scenario : 'crosschain',
        shock: shockPct / 100,
        exitHaircut: scenario === 'backing' ? haircutPct / 100 : 0,
        oracleTracksBacking: oracleTracks,
      })
    : null
  const sym = model.loanSymbol

  const verdict = !applicable || !result ? 'NOT_APPLICABLE' : result.liquidatable ? 'LIQUIDATABLE · HF < 1' : result.estimatedLoss > 0 ? 'NO LIQUIDATION · VALUE DOWN' : 'HEALTHY'
  const verdictColor = !applicable || !result ? '#94a3b8' : result.liquidatable ? '#f87171' : result.estimatedLoss > 0 ? '#fbbf24' : '#34d399'
  const curated = path?.some((s) => s.via && s.via.status !== 'ONCHAIN_VERIFIED') ?? false

  return (
    <div className="grid flex-1 grid-cols-1 items-start gap-4 p-4 lg:grid-cols-[minmax(300px,380px)_minmax(0,1fr)]">
      <Card label="Scenario" className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-2">
          <SectionLabel>Scenario</SectionLabel>
          <EstimateBadge text="CLIENT ESTIMATE" />
        </div>
        <div role="group" aria-label="Scenario" className="flex flex-col gap-2">
          {SCENARIOS.map((s) => (
            <button
              key={s.id}
              type="button"
              aria-pressed={s.id === scenario}
              onClick={() => setScenario(s.id)}
              className="flex cursor-pointer flex-col items-start gap-1 rounded-lg border border-line bg-control p-3 text-left hover:border-[#3a4766] aria-pressed:border-accent aria-pressed:bg-[#1a2140]"
            >
              <span className="font-medium">{s.title}</span>
              <span className="text-[11.5px] leading-snug text-ink-3">{s.description}</span>
            </button>
          ))}
        </div>
        <PriceField model={model} />
        {applicable && model.inputs && (
          <>
            <div className="flex flex-col gap-1">
              <label htmlFor="shock" className="flex justify-between text-xs text-ink-2">
                <span>{spec.shockLabel}</span>
                <span className="font-mono text-ink">−{shockPct} %</span>
              </label>
              <input id="shock" type="range" min={0} max={40} step={1} value={shockPct} onChange={(e) => setShockPct(Number(e.target.value))} />
              <div className="flex justify-between text-[10px] text-ink-4">
                <span>0 %</span>
                <span>HF = 1 at −{result ? pct(result.liquidationDistance, 1).replace(' %', '') : '?'} %</span>
                <span>40 %</span>
              </div>
            </div>
            {scenario === 'backing' && (
              <>
                <div className="flex flex-col gap-1">
                  <label htmlFor="haircut" className="flex justify-between text-xs text-ink-2">
                    <span>Extra exit haircut (AMM)</span>
                    <span className="font-mono text-ink">{haircutPct} %</span>
                  </label>
                  <input id="haircut" type="range" min={0} max={10} step={0.5} value={haircutPct} onChange={(e) => setHaircutPct(Number(e.target.value))} />
                </div>
                <div className="flex flex-col gap-1.5">
                  <span className="text-xs text-ink-2">
                    Does the collateral oracle follow the backing?
                  </span>
                  <Segmented<'no' | 'yes'>
                    label="Does the collateral oracle follow the backing?"
                    value={oracleTracks ? 'yes' : 'no'}
                    onChange={(v) => setOracleTracks(v === 'yes')}
                    options={[
                      { value: 'no', label: 'No' },
                      { value: 'yes', label: 'Yes' },
                    ]}
                  />
                  <span className="text-[10.5px] leading-snug text-ink-4">The API does not serve the oracle design: this is your assumption.</span>
                </div>
              </>
            )}
          </>
        )}
        {model.problem && <Notes items={[model.problem]} />}
      </Card>

      <div className="flex min-w-0 flex-col gap-4">
        <Card label="Propagation path" className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <SectionLabel>Propagation path</SectionLabel>
            <a href="#/graph" className="text-xs">
              Open in Dependency Graph
            </a>
          </div>
          {path ? (
            <>
              <PathFlow steps={path} states={result ? stepStates(path, scenario, result, oracleTracks) : path.map(() => 'unchanged')} />
              <ol className="m-0 flex list-none flex-col gap-1 p-0 text-xs">
                {path.map((s, i) => (
                  <li key={s.node.id} className="flex flex-wrap items-center gap-2">
                    <span className="w-5 font-mono text-ink-4">{i + 1}.</span>
                    <span className="text-ink">{s.node.label}</span>
                    {s.via && (
                      <>
                        <span className="font-mono text-[10.5px] text-ink-4">→ {s.via.type}</span>
                        <VerificationBadge status={s.via.status} />
                      </>
                    )}
                  </li>
                ))}
              </ol>
              <div className="flex flex-wrap gap-4 text-[11px] text-ink-3">
                {(['shocked', 'impaired', 'unchanged'] as const).map((k) => (
                  <span key={k} className="inline-flex items-center gap-1.5">
                    <span className="size-2.5 rounded-full" style={{ background: STEP_COLOR[k] }} />
                    {k === 'unchanged' ? 'Unchanged at oracle' : k[0]!.toUpperCase() + k.slice(1)}
                  </span>
                ))}
              </div>
              {curated && <Notes items={['This path crosses relations that are not verified on-chain (CURATED or UNRESOLVED): treat it as a hypothesis.']} />}
            </>
          ) : (
            <p role="note" className="m-0 rounded-md border border-dashed border-[#475569] p-3 text-xs leading-relaxed text-ink-2">
              NOT_APPLICABLE: no relation in this strategy’s graph connects this scenario to the position. No impact is computed rather than a guess.
            </p>
          )}
        </Card>

        <Card label="Before and after" className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <SectionLabel>Before / after</SectionLabel>
            <span className="font-mono text-xs font-medium" style={{ color: verdictColor }}>
              {verdict}
            </span>
          </div>
          {result && applicable ? (
            <>
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr className="text-left text-ink-3">
                      <th scope="col" className="py-2 pr-3 font-medium">Metric</th>
                      <th scope="col" className="py-2 pr-3 text-right font-medium">Before</th>
                      <th scope="col" className="py-2 pr-3 text-right font-medium">After</th>
                      <th scope="col" className="py-2 text-right font-medium">Δ</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {[
                      ['Collateral (oracle value)', amount(result.oracleValueBefore, sym), amount(result.oracleValueAfter, sym), signed(result.oracleValueAfter - result.oracleValueBefore, sym), result.oracleValueAfter < result.oracleValueBefore],
                      ['LTV', pct(result.ltvBefore, 1), pct(result.ltvAfter, 1), `${((result.ltvAfter - result.ltvBefore) * 100).toFixed(1)} pp`, result.ltvAfter > result.ltvBefore],
                      ['Health factor', result.hfBefore.toFixed(3), result.hfAfter.toFixed(3), (result.hfAfter - result.hfBefore).toFixed(3), result.hfAfter < 1],
                      ['Exit value (tradable)', amount(result.exitValueBefore, sym), amount(result.exitValueAfter, sym), signed(result.exitValueAfter - result.exitValueBefore, sym), result.exitValueAfter < result.exitValueBefore],
                      ['Estimated loss', amount(0, sym), amount(result.estimatedLoss, sym), '', result.estimatedLoss > 0],
                    ].map(([label, before, after, delta, bad]) => (
                      <tr key={String(label)} className="border-t border-[#1f2940]">
                        <th scope="row" className="py-2 pr-3 text-left font-sans font-normal text-ink-2">{label}</th>
                        <td className="py-2 pr-3 text-right">{before}</td>
                        <td className="py-2 pr-3 text-right" style={{ color: bad ? '#f87171' : '#e2e8f0' }}>{after}</td>
                        <td className="py-2 text-right text-ink-3">{delta}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="m-0 text-xs leading-relaxed text-ink-3">
                {scenario === 'oracle'
                  ? `The oracle value falls by the shock; the tradable value does not. If the health factor drops below 1, the loss assumes a full liquidation with the Morpho incentive (${((liquidationIncentive(model.inputs!.lltv) - 1) * 100).toFixed(2)} %).`
                  : 'The tradable value of the collateral falls by the shock and the AMM haircut. The oracle only moves if you assume it follows the backing.'}
              </p>
            </>
          ) : (
            <p className="m-0 text-xs text-ink-3">{model.problem ?? 'No impact computed for this scenario.'}</p>
          )}
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line-soft pt-3 text-[11px] text-ink-4">
            <span>Computed in the browser: POST /simulations is not implemented in the API.</span>
            <a href="#/decision" className="text-xs">
              Go to STAY / EXIT
            </a>
          </div>
        </Card>
      </div>
    </div>
  )
}
