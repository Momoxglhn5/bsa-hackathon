import { useEffect, useMemo, useRef } from 'react'
import { SOURCES } from './api/client'
import { ErrorBox, Kicker, Seg, Spinner } from './components/ui'
import { DecideTab } from './decide/DecideTab'
import { DrawerHost } from './drawers/Drawers'
import { ExploreTab } from './explore/ExploreTab'
import { GraphPage } from './graph/GraphPage'
import type { SceneGraph } from './intro/scene'
import { useDive, type DiveRefs } from './intro/useDive'
import { SimulateTab } from './simulate/SimulateTab'
import { RadarProvider, useRadar, type Tab } from './state/radar'

export default function App() {
  return (
    <RadarProvider>
      <Shell />
    </RadarProvider>
  )
}

function Grain() {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const c = document.createElement('canvas')
    c.width = c.height = 180
    const x = c.getContext('2d')
    if (!x || !ref.current) return
    const d = x.createImageData(180, 180)
    for (let i = 0; i < d.data.length; i += 4) {
      const v = Math.random() * 255
      d.data[i] = d.data[i + 1] = d.data[i + 2] = v
      d.data[i + 3] = 40
    }
    x.putImageData(d, 0, 0)
    ref.current.style.backgroundImage = `url(${c.toDataURL()})`
  }, [])
  return <div ref={ref} className="grain" aria-hidden="true" />
}

function Shell() {
  const r = useRadar()
  const refs: DiveRefs = {
    mount: useRef<HTMLDivElement>(null), input: useRef<HTMLDivElement>(null), hero: useRef<HTMLElement>(null),
    cue: useRef<HTMLDivElement>(null), dive: useRef<HTMLDivElement>(null), gate: useRef<HTMLDivElement>(null),
    topSea: useRef<HTMLDivElement>(null), topDash: useRef<HTMLDivElement>(null), dash: useRef<HTMLElement>(null),
  }
  const stableRefs = useRef(refs).current
  const sceneGraph = useMemo<SceneGraph | null>(() => {
    const graphs = Object.values(r.loaded.graphs).map((g) => g.data)
    if (!graphs.length) return null
    const nodes = new Map<string, SceneGraph['nodes'][number]>()
    const edges: SceneGraph['edges'] = []
    for (const g of graphs) {
      const unresolved = new Set(g.edges.filter((e) => e.verification_status === 'UNRESOLVED').flatMap((e) => [e.target]))
      for (const n of g.nodes) if (!nodes.has(n.id)) nodes.set(n.id, { id: n.id, type: n.type, unresolved: unresolved.has(n.id) })
      for (const e of g.edges) edges.push({ s: e.source, t: e.target, status: e.verification_status })
    }
    return { nodes: [...nodes.values()], edges, positions: [...nodes.values()].filter((n) => n.type === 'POSITION').map((n) => n.id) }
  }, [r.loaded.graphs])
  const dive = useDive(stableRefs, sceneGraph, r.page === 'graph')

  return (
    <div className="stage">
      {dive.noWebgl && <div className="no-webgl" aria-hidden="true" />}
      <div ref={stableRefs.mount} className="stage" aria-hidden="true" />
      <div className="vignette" aria-hidden="true" />
      <Grain />
      <div ref={stableRefs.input} className="input-layer" aria-hidden="true" />

      <section ref={stableRefs.hero} className="hero" aria-hidden={dive.inDash}>
        <div className="kicker">DEFINE</div>
        <h1>An X-Ray for <span style={{ fontStyle: 'italic' }}>DeFi Positions</span></h1>
        <div className="gold-rule" style={{ marginTop: 16 }} />
      </section>
      <div ref={stableRefs.cue} className="cue" aria-hidden="true"><span>SCROLL</span><span /></div>
      <div ref={stableRefs.dive} className="dive-text" aria-hidden="true">Beneath every position, its dependencies.</div>
      <div ref={stableRefs.gate} className="gate">
        <button type="button" className="gate-btn" onClick={dive.enter}>
          <span className="kicker">ENTER</span>
          <span className="gate-title">Open the dashboard<span className="gate-circle">→</span></span>
          <span className="gold-rule" />
        </button>
      </div>

      <main ref={stableRefs.dash} className="dashboard" data-scroll="1" aria-hidden={!dive.inDash} inert={!dive.inDash}>
        <div className="dash-inner">
          <Dashboard />
        </div>
      </main>

      {r.page === 'graph' && r.sel && <GraphPage />}
      <DrawerHost />

      <header className="topbar">
        <div className="brand">
          <img src="/logo-mark.png" alt="" width={26} height={26} />
          <span style={{ width: 1, height: 16, background: 'rgba(212,180,122,0.35)' }} />
          <span className="brand-name">DeFiNe</span>
        </div>
        <div ref={stableRefs.topSea} className="row" style={{ pointerEvents: 'auto' }}>
          <button type="button" className="dive-btn" onClick={dive.toGate} tabIndex={dive.inDash ? -1 : 0}>DIVE IN</button>
        </div>
        <div ref={stableRefs.topDash} className="dash-controls" inert={!dive.inDash}>
          <TopControls onSurface={dive.surface} />
        </div>
      </header>
    </div>
  )
}

function TopControls({ onSurface }: { onSurface: () => void }) {
  const r = useRadar()
  const tabs: [Tab, string][] = [['explore', 'Explore'], ['simulate', 'Simulate'], ['decide', 'Decide']]
  return (
    <>
      <button type="button" className="source-chip" data-source={r.source.id} title="Dataset" onClick={() => r.setDrawer({ kind: 'data' })}>
        {r.source.id === 'demo' ? 'DEMO' : 'LIVE MARKET'}
      </button>
      <label className="pos-select">
        <span className="sr-only">Position</span>
        <select value={r.sel?.id ?? ''} onChange={(e) => r.select(e.target.value)} disabled={!r.positions.length} aria-label="Position">
          {r.positions.map((p) => <option key={p.id} value={p.id} style={{ background: '#04101F' }}>{p.pair} · {p.title}</option>)}
          {!r.positions.length && <option value="">No position</option>}
        </select>
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><path d="M2 3.5 5 6.5 8 3.5" /></svg>
      </label>
      <button type="button" className="graph-btn" title="Open the dependency graph" disabled={!r.sel}
        onClick={() => { r.setPgNode(null); r.showPath(null); r.setPage('graph') }}>
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><circle cx="8" cy="8" r="2.2" /><circle cx="2.5" cy="3" r="1.5" /><circle cx="13.5" cy="3.5" r="1.5" /><circle cx="3" cy="13" r="1.5" /><circle cx="13" cy="12.5" r="1.5" /><path d="M6.3 6.6 3.6 4.1M9.7 6.7l2.6-2.2M6.4 9.5l-2.3 2.4M9.7 9.3l2.3 2.3" /></svg>
        <span className="lbl">Graph</span>
      </button>
      <nav className="tabs" aria-label="Screens">
        {tabs.map(([id, label]) => (
          <button key={id} type="button" className="seg" aria-current={r.tab === id ? 'page' : undefined}
            onClick={() => { r.setTab(id); r.setDrawer(null); r.setPage('dash') }}>{label}</button>
        ))}
      </nav>
      <button type="button" className="surface-btn" onClick={onSurface}>↑ SURFACE</button>
    </>
  )
}

function Dashboard() {
  const r = useRadar()
  const top = useRef<HTMLDivElement>(null)
  useEffect(() => { top.current?.closest('.dashboard')?.scrollTo({ top: 0 }) }, [r.tab])

  if (r.status === 'loading' && !r.loaded.catalog) {
    return <div ref={top} className="panel" style={{ padding: 28 }}><Spinner label="Loading…" /></div>
  }
  if (r.status === 'error' || !r.loaded.catalog) {
    return (
      <div ref={top} className="panel stack gap-14" style={{ padding: 28 }}>
        <Kicker gold>SERVICE UNAVAILABLE</Kicker>
        <span className="serif" style={{ fontSize: 28 }}>Data not loaded</span>
        <ErrorBox message={r.error ?? 'Catalog unavailable.'} onRetry={() => void r.reload()} />
        <Seg label="Dataset" value={r.source.id} onChange={(id) => r.setSourceId(id)} options={SOURCES.map((s) => ({ id: s.id, label: s.label }))} />
      </div>
    )
  }
  if (!r.sel) {
    return (
      <div ref={top} className="panel stack gap-10" style={{ padding: 28 }}>
        <Kicker gold>NO POSITION</Kicker>
        <span className="serif" style={{ fontSize: 24 }}>This dataset contains no position.</span>
        <Seg label="Dataset" value={r.source.id} onChange={(id) => r.setSourceId(id)} options={SOURCES.map((s) => ({ id: s.id, label: s.label }))} />
      </div>
    )
  }
  return (
    <>
      <div ref={top} />
      {r.tab === 'explore' && <ExploreTab />}
      {r.tab === 'simulate' && <SimulateTab />}
      {r.tab === 'decide' && <DecideTab />}
    </>
  )
}
