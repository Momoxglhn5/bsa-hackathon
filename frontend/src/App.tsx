import { useEffect, useState } from 'react'
import { RadarContext, useRadarLoader } from './data/useRadar'
import type { RadarData } from './data/useRadar'
import { DATA_MODE } from './lib/api'
import { utc } from './lib/format'
import DecisionScreen from './screens/DecisionScreen'
import GraphScreen from './screens/GraphScreen'
import StressScreen from './screens/StressScreen'
import { StatusBadge } from './ui/primitives'

const SCREENS = [
  { hash: '#/graph', n: '01', label: 'Dependency Graph' },
  { hash: '#/stress', n: '02', label: 'Stress Simulator' },
  { hash: '#/decision', n: '03', label: 'STAY / EXIT Decision' },
] as const
type ScreenHash = (typeof SCREENS)[number]['hash']

function currentHash(): ScreenHash {
  const h = window.location.hash
  return SCREENS.some((s) => s.hash === h) ? (h as ScreenHash) : '#/graph'
}

function Logo() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#818CF8" strokeWidth="1.8" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="4.5" />
      <path d="M12 12l6.4-6.4" />
    </svg>
  )
}

function ContextBar({ data, onStrategy }: { data: RadarData; onStrategy: (id: string) => void }) {
  const s = data.detail.data.strategy
  const position = data.position?.data
  return (
    <div className="flex flex-wrap items-center gap-2.5 border-b border-line-soft bg-bar px-4 py-2 text-xs">
      <label htmlFor="strategy" className="text-ink-3">
        Strategy
      </label>
      <select
        id="strategy"
        value={s.strategy_id}
        onChange={(e) => onStrategy(e.target.value)}
        className="h-7 max-w-[320px] rounded-md border border-[#243049] bg-control px-2 text-ink"
      >
        {data.strategies.data.map((st) => (
          <option key={st.strategy_id} value={st.strategy_id}>
            {st.pt.symbol ?? 'PT'} → {st.loan_asset.symbol ?? 'loan'} · {st.status}
          </option>
        ))}
      </select>
      <span className="inline-flex h-[26px] items-center gap-1.5 rounded-full border border-[#243049] px-2.5 text-ink-2">
        <span className="size-1.5 rounded-full bg-accent-2" />
        Ethereum Mainnet
      </span>
      {position?.kind === 'SIMULATED' && (
        <span className="inline-flex h-[26px] items-center rounded-full border border-dashed border-[#a5b4fc] px-2.5 font-mono text-[11px] tracking-wide text-[#c7d2fe]">
          SIMULATED POSITION
        </span>
      )}
      <span className="text-ink-3">
        As of <span className="font-mono text-ink-2">{utc(data.detail.as_of)}</span>
      </span>
      <StatusBadge status={data.detail.status} />
      {data.detail.snapshot.data_mode === 'DEMO' && (
        <span className="ml-auto rounded border border-warn-line px-2 py-0.5 font-mono text-[11px] text-warn">
          DEMO data: simulated, not real
        </span>
      )}
    </div>
  )
}

function Unavailable({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="mx-auto mt-24 flex max-w-lg flex-col gap-3 rounded-lg border border-line bg-card p-6">
      <h1 className="m-0 text-base font-semibold">Data API unavailable</h1>
      <p className="m-0 leading-relaxed text-ink-2">{message}</p>
      <p className="m-0 text-xs leading-relaxed text-ink-3">
        Start the backend (see README) and check <code className="font-mono">VITE_API_BASE_URL</code>. Requested
        data mode: <span className="font-mono">{DATA_MODE}</span>.
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="h-9 w-fit cursor-pointer rounded-md border border-accent bg-accent-soft px-4 text-ink hover:bg-[#323a6e]"
      >
        Retry
      </button>
    </div>
  )
}

export default function App() {
  const [hash, setHash] = useState<ScreenHash>(currentHash)
  const [strategyId, setStrategyId] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const result = useRadarLoader(strategyId, attempt)

  useEffect(() => {
    const onHash = () => setHash(currentHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex min-h-12 flex-wrap items-stretch gap-5 border-b border-line-soft px-4">
        <div className="flex items-center gap-2.5 text-sm font-semibold">
          <Logo />
          DeFi Risk Radar
        </div>
        <nav aria-label="Screens" className="flex flex-wrap gap-0.5">
          {SCREENS.map((s) => {
            const active = s.hash === hash
            return (
              <a
                key={s.hash}
                href={s.hash}
                aria-current={active ? 'page' : undefined}
                className={`flex items-center border-b-2 px-3 text-[13px] no-underline hover:text-ink ${
                  active ? 'border-accent text-ink' : 'border-transparent text-ink-3'
                }`}
              >
                <span className={`mr-2 font-mono ${active ? 'text-accent-2' : ''}`}>{s.n}</span>
                {s.label}
              </a>
            )
          })}
        </nav>
        <div className="ml-auto flex items-center font-mono text-[11px] text-ink-4">data-contract-v1.0 · /api/v1</div>
      </header>

      {result.state === 'loading' && (
        <div role="status" className="flex flex-1 items-center justify-center text-ink-3">
          Loading strategy, markets and relations…
        </div>
      )}
      {result.state === 'error' && <Unavailable message={result.error.message} onRetry={() => setAttempt((a) => a + 1)} />}
      {result.state === 'ready' && (
        <RadarContext.Provider value={result.data}>
          <ContextBar data={result.data} onStrategy={setStrategyId} />
          <main className="flex min-h-0 flex-1 flex-col">
            {hash === '#/graph' && <GraphScreen />}
            {hash === '#/stress' && <StressScreen />}
            {hash === '#/decision' && <DecisionScreen />}
          </main>
        </RadarContext.Provider>
      )}
    </div>
  )
}
