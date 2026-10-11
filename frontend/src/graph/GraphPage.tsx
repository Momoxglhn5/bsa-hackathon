import { useEffect, useMemo, useRef } from 'react'
import { getExposure } from '../api/endpoints'
import type { CoreEnvelope, DependencyExposure, GraphDTO, ScenarioType } from '../api/types'
import { ErrorBox, Kicker, ListRow, Spinner, Tag } from '../components/ui'
import { fmtUsd } from '../lib/format'
import { C, CRITICALITY_LABEL, FAILURE_MODE_LABEL, LINK_STATUS, NODE_TYPE_LABEL, SCENARIOS } from '../lib/labels'
import { VIGILANCE_COLOR, VIGILANCE_LABEL, nodeNames, reach, vigilance, type NodeInfo, type Vigilance } from '../lib/model'
import { useRadar } from '../state/radar'
import { useRequest } from '../state/useRequest'
import { ForceGraph, type FGEdge, type FGNode } from './forceGraph'

interface View {
  nodes: FGNode[]
  edges: FGEdge[]
  posFocus: Map<string, Set<string>>
  names: Map<string, NodeInfo>
  graphOf: Map<string, GraphDTO>
  depth: Map<string, number>
  shared: Map<string, number>
}

function useGraphView(): View | null {
  const r = useRadar()
  const sel = r.sel
  const cat = r.loaded.catalog?.data ?? null
  return useMemo(() => {
    if (!sel) return null
    const graphs = r.pgAll ? Object.values(r.loaded.graphs).map((e) => e.data) : [r.graphFor(sel.strategyId)].filter((g): g is GraphDTO => !!g)
    if (!graphs.length) return null
    const names = new Map<string, NodeInfo>()
    const graphOf = new Map<string, GraphDTO>()
    for (const g of graphs) {
      for (const [k, v] of nodeNames(g, r.positions, cat)) if (!names.has(k)) names.set(k, v)
      for (const n of g.nodes) if (!graphOf.has(n.id)) graphOf.set(n.id, g)
    }
    const posFocus = new Map<string, Set<string>>()
    const depth = new Map<string, number>()
    const shared = new Map<string, number>()
    const roots = r.pgAll ? graphs.flatMap((g) => g.nodes.filter((n) => n.type === 'POSITION').map((n) => ({ id: n.id, g }))) : [{ id: sel.id, g: graphs[0] }]
    for (const { id, g } of roots) {
      const R = reach(g, id)
      posFocus.set(id, new Set(R.keys()))
      R.forEach((d, k) => depth.set(k, Math.min(depth.get(k) ?? 99, d)))
    }
    for (const g of graphs) {
      const positions = g.nodes.filter((n) => n.type === 'POSITION').map((n) => reach(g, n.id))
      for (const n of g.nodes) shared.set(n.id, positions.filter((m) => m.has(n.id)).length)
    }
    const keep = new Set(depth.keys())
    const unresolved = new Set(graphs.flatMap((g) => g.edges.filter((e) => e.verification_status === 'UNRESOLVED').flatMap((e) => [e.source, e.target])))
    const seenEdge = new Set<string>()
    const edges: FGEdge[] = []
    for (const g of graphs) for (const e of g.edges) {
      if (!keep.has(e.source) || !keep.has(e.target) || seenEdge.has(e.id)) continue
      seenEdge.add(e.id)
      edges.push({ s: e.source, t: e.target, status: e.verification_status, required: e.required })
    }
    const nodes: FGNode[] = [...keep].map((id) => {
      const g = graphOf.get(id)!
      const n = g.nodes.find((x) => x.id === id)!
      const isPos = n.type === 'POSITION'
      const pin = r.pgAll ? isPos : id === sel.id
      const d = depth.get(id) ?? 1
      const info = names.get(id)
      // A fund can hold several markets: the overview names each position by holder and PT.
      const pv = r.pgAll && isPos ? r.positions.find((p) => p.id === id) : undefined
      return {
        id, isPos, pin, depth: pin ? 0 : d, unresolved: unresolved.has(id),
        name: pv ? `${pv.title} · ${pv.ptName}` : info?.short ?? id,
        typeLabel: NODE_TYPE_LABEL[n.type],
        vig: (pin || (r.pgAll && isPos)) ? 'pos' : vigilance(g, id, d, shared.get(id) ?? 1),
      }
    })
    return { nodes, edges, posFocus, names, graphOf, depth, shared }
  }, [r.pgAll, r.loaded.graphs, r.graphFor, r.positions, sel, cat])
}

export function GraphPage() {
  const r = useRadar()
  const sel = r.sel!
  const view = useGraphView()
  const cvRef = useRef<HTMLCanvasElement>(null)
  const fg = useRef<ForceGraph | null>(null)
  const backRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!cvRef.current) return
    const g = new ForceGraph(cvRef.current, (id) => r.setPgNode(id))
    fg.current = g
    return () => { g.destroy(); fg.current = null }
  }, [])
  useEffect(() => { if (view) fg.current?.setData(view.nodes, view.edges, r.pgAll, view.posFocus) }, [view, r.pgAll])
  useEffect(() => { fg.current?.setSelected(r.pgNode) }, [r.pgNode])
  useEffect(() => { fg.current?.setPath(r.pgAll ? null : r.pgPath) }, [r.pgPath, r.pgAll])
  // Escape closes the node panel, then the page; a drawer above handles its own Escape.
  const escState = useRef({ drawer: r.drawer, pgNode: r.pgNode })
  escState.current = { drawer: r.drawer, pgNode: r.pgNode }
  const { setPgNode, setPage } = r
  useEffect(() => {
    backRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || escState.current.drawer) return
      if (escState.current.pgNode) setPgNode(null)
      else setPage('dash')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setPgNode, setPage])

  const posCount = view?.nodes.filter((n) => n.isPos).length ?? 0
  const stats = !view ? '' : r.pgAll ? `${posCount} POSITIONS · ${view.nodes.length} NODES · ${view.edges.length} RELATIONS`
    : `${view.nodes.length} COMPONENTS · ${view.edges.length} RELATIONS`

  return (
    <div className="graph-page" data-scroll="1" role="dialog" aria-modal="true" aria-label="Dependency graph">
      <header className="row wrap between gap-12" style={{ columnGap: 20 }}>
        <div className="row wrap gap-16">
          <button ref={backRef} type="button" className="btn" onClick={() => { r.setPage('dash'); r.showPath(null) }}>← Back to Dashboard</button>
          <div className="stack" style={{ gap: 3 }}>
            <Kicker gold>DEPENDENCY GRAPH</Kicker>
            <span className="serif" style={{ fontSize: 24, lineHeight: 1.1 }}>{r.pgAll ? 'All positions' : `${sel.title} · ${sel.pair}`}</span>
          </div>
        </div>
        <div className="row wrap gap-12">
          <span className="kicker">{stats}</span>
          <select className="select" style={{ width: 'auto', fontFamily: 'var(--serif)', fontSize: 17 }} aria-label="Position"
            value={r.pgAll ? '__all' : sel.id}
            onChange={(e) => {
              const v = e.target.value
              r.setPgNode(null)
              r.showPath(null)
              if (v === '__all') r.setPgAll(true)
              else { r.setPgAll(false); r.select(v) }
            }}>
            <option value="__all">All positions</option>
            {r.positions.map((p) => <option key={p.id} value={p.id}>{p.title} · {p.pair}</option>)}
          </select>
          {view && (
            <select className="select" style={{ width: 'auto', maxWidth: 220 }} aria-label="Go to component" value={r.pgNode ?? ''}
              onChange={(e) => { const id = e.target.value || null; r.setPgNode(id); if (id) fg.current?.focusOn(id) }}>
              <option value="">Go to component…</option>
              {view.nodes.map((n) => <option key={n.id} value={n.id}>{view.names.get(n.id)?.name ?? n.id}</option>)}
            </select>
          )}
        </div>
      </header>
      <section className="graph-stage">
        <canvas ref={cvRef} aria-label="Dependency graph: use the “Go to component” list to navigate with the keyboard" />
        {!view && <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', fontSize: 13, color: C.sec }}>{r.loaded.graphErrors[sel.strategyId] ?? 'Graph unavailable.'}</div>}
        <div className="stack gap-6" style={{ position: 'absolute', left: 16, top: 16 }}>
          <button type="button" className="btn btn--icon" aria-label="Zoom in" onClick={() => fg.current?.zoom(1.25)}>+</button>
          <button type="button" className="btn btn--icon" aria-label="Zoom out" onClick={() => fg.current?.zoom(0.8)}>−</button>
          <button type="button" className="btn btn--icon" aria-label="Fit to screen" onClick={() => fg.current?.fit()}>⤢</button>
        </div>
        {r.pgPath && !r.pgAll && (
          <div className="alert alert--info row gap-10" style={{ position: 'absolute', left: 64, top: 16, padding: '8px 12px' }}>
            <span style={{ fontSize: 11 }}>Impact path</span>
            <button type="button" className="btn--link" style={{ fontSize: 11 }} onClick={() => r.showPath(null)}>Clear</button>
          </div>
        )}
        <Legend all={r.pgAll} />
        <div className="kicker" style={{ position: 'absolute', right: 16, bottom: 16, letterSpacing: '0.1em', pointerEvents: 'none' }}>DRAG · SCROLL · CLICK FOR DETAILS</div>
        {r.pgNode && view && <NodePanel id={r.pgNode} view={view} />}
      </section>
    </div>
  )
}

function Legend({ all }: { all: boolean }) {
  const dot = (c: string) => <span style={{ width: 10, height: 10, borderRadius: '50%', border: `1.5px solid ${c}`, background: `${c}40` }} />
  const line = (s: keyof typeof LINK_STATUS, dash: string) => (
    <span className="legend-item"><svg width="22" height="6" aria-hidden="true"><line x1="0" y1="3" x2="22" y2="3" stroke={LINK_STATUS[s].color} strokeWidth="1.6" strokeDasharray={dash} /></svg>{LINK_STATUS[s].label}</span>
  )
  return (
    <div className="legend">
      <Kicker>LEGEND</Kicker>
      <span className="legend-item"><span style={{ width: 12, height: 12, borderRadius: '50%', background: C.gold }} />{all ? 'Position' : 'Selected position'}</span>
      <span className="legend-item">{dot(VIGILANCE_COLOR.low)}Low risk</span>
      <span className="legend-item">{dot(VIGILANCE_COLOR.med)}Medium risk</span>
      <span className="legend-item">{dot(VIGILANCE_COLOR.high)}High risk</span>
      {!all && (
        <>
          {line('CONFIRMED', '')}
          {line('OBSERVED', '5 3')}
          {line('HYPOTHESIS', '8 4')}
          {line('UNRESOLVED', '1.5 3')}
        </>
      )}
      <span className="legend-item" style={{ color: C.unk }}>A → B: A depends on B</span>
    </div>
  )
}

const SHOCK_FOR: Partial<Record<string, ScenarioType>> = {
  ORACLE: 'ORACLE_MANIPULATION', ASSET: 'UNAUTHORIZED_MINTING', SY: 'UNAUTHORIZED_MINTING', PROTOCOL: 'UNAUTHORIZED_MINTING',
  CONTRACT: 'UNAUTHORIZED_MINTING', BRIDGE: 'CROSS_CHAIN_FAILURE',
}

function NodePanel({ id, view }: { id: string; view: View }) {
  const r = useRadar()
  const sel = r.sel!
  const g = view.graphOf.get(id)
  const n = g?.nodes.find((x) => x.id === id)
  const info = view.names.get(id)
  const exposure = useRequest<CoreEnvelope<DependencyExposure>>()
  const closeRef = useRef<HTMLButtonElement>(null)
  useEffect(() => { exposure.reset() }, [id])
  useEffect(() => { closeRef.current?.focus() }, [id])
  if (!g || !n) return null
  const isPos = n.type === 'POSITION'
  const depth = view.depth.get(id) ?? 0
  const vig: Vigilance | null = isPos ? null : vigilance(g, id, depth, view.shared.get(id) ?? 1)
  const rels = g.edges.filter((e) => (e.source === id || e.target === id) && view.depth.has(e.source) && view.depth.has(e.target))
  const modes = [...new Set(g.edges.filter((e) => e.target === id).flatMap((e) => e.failure_modes))].map((m) => FAILURE_MODE_LABEL[m] ?? m)
  const totalPositions = g.nodes.filter((x) => x.type === 'POSITION').length || 1
  const unresolved = view.nodes.find((x) => x.id === id)?.unresolved ?? false
  const shock = SHOCK_FOR[n.type]
  const canShock = !!shock && !isPos && (r.loaded.scenarios.length === 0 || r.loaded.scenarios.includes(shock)) && reach(g, sel.id).has(id)
  const protocol = id.startsWith('morpho:') ? ' · MORPHO BLUE' : id.startsWith('pendle:') ? ' · PENDLE V2' : ''
  const color = isPos ? C.gold : VIGILANCE_COLOR[vig!]
  const ex = exposure.data?.data
  const pv = isPos ? r.positions.find((p) => p.id === id) : undefined

  return (
    <aside className="node-panel" aria-label={`Details: ${info?.name ?? id}`}>
      <div className="stack gap-8" style={{ padding: '18px 20px 14px', borderBottom: '1px solid rgba(163,180,202,0.1)' }}>
        <div className="row between gap-10">
          <Kicker gold>{NODE_TYPE_LABEL[n.type].toUpperCase()}{protocol}</Kicker>
          <button ref={closeRef} type="button" aria-label="Close" onClick={() => r.setPgNode(null)}
            style={{ width: 28, height: 28, borderRadius: '50%', border: '1px solid rgba(163,180,202,0.2)', background: 'transparent', color: C.sec, cursor: 'pointer' }}>×</button>
        </div>
        <span className="serif" style={{ fontSize: 26, lineHeight: 1.1, textWrap: 'pretty' }}>{info?.name ?? id}</span>
        <span className="mono" style={{ alignSelf: 'flex-start', fontSize: 10, letterSpacing: '0.1em', padding: '3px 10px', borderRadius: 999, color, border: `1px solid ${color}` }}>
          {isPos ? (id === sel.id ? 'Analyzed position' : 'Position') : VIGILANCE_LABEL[vig!]}
        </span>
      </div>
      <div className="stack gap-16" style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '14px 20px 20px' }}>
        <div className="stack">
          {pv ? (
            <>
              <ListRow k="Collateral" v={fmtUsd(r.loaded.risks[pv.strategyId]?.data.position_risk[pv.id]?.collateral_value_usd)} mono />
              <ListRow k="Equity" v={fmtUsd(r.loaded.risks[pv.strategyId]?.data.position_risk[pv.id]?.equity_usd)} mono />
              <ListRow k="Dependencies" v={String(Math.max(0, reach(g, id).size - 1))} mono />
            </>
          ) : (
            <>
              <ListRow k="Distance to position" v={`${depth} step${depth > 1 ? 's' : ''}`} mono />
              <ListRow k="Dependent positions" v={`${view.shared.get(id) ?? 1} / ${totalPositions}`} mono />
              {modes.length > 0 && <ListRow k="Transmission" v={modes.join(', ')} />}
              {unresolved && <ListRow k="Link" v="Unresolved" vColor={C.cau} />}
            </>
          )}
          <span className="mono" style={{ fontSize: 10, color: C.unk, overflowWrap: 'anywhere', paddingTop: 8 }}>{id}</span>
        </div>
        <div className="stack">
          <Kicker>RELATIONS</Kicker>
          {rels.map((e) => {
            const out = e.source === id
            const other = out ? e.target : e.source
            const st = LINK_STATUS[e.verification_status]
            return (
              <button key={e.id} type="button" className="rel-btn" onClick={() => r.setPgNode(other)}>
                <span>{out ? '→ depends on ' : '← used by '}{view.names.get(other)?.name ?? other}</span>
                <span className="row wrap gap-8">
                  <Tag color={e.criticality === 'CRITICAL' ? C.dan : C.unk}>{CRITICALITY_LABEL[e.criticality] ?? e.criticality}</Tag>
                  <Tag color={st.color}>{st.short}</Tag>
                </span>
              </button>
            )
          })}
        </div>
        <div className="row wrap gap-8">
          <button type="button" className="btn" onClick={() => r.setDrawer({ kind: 'evidence', title: info?.name ?? id, ids: [...new Set(rels.flatMap((e) => e.evidence_ids))] })}>Evidence</button>
          {!isPos && <button type="button" className="btn" disabled={exposure.loading} onClick={() => exposure.run((signal) => getExposure(r.source, g.strategy_id, id, { signal }))}>Exposed positions</button>}
          {canShock && <button type="button" className="btn btn--primary" style={{ height: 34 }} onClick={() => r.prefillShock(id, shock!)}>Simulate · {SCENARIOS[shock!].label}</button>}
        </div>
        {exposure.loading && <Spinner label="Computing…" />}
        {exposure.error && <ErrorBox message={exposure.error} />}
        {ex && (
          <div className="subcard stack gap-6">
            <Kicker>EXPOSURE</Kicker>
            <ListRow k="Positions" v={String(ex.unique_position_count)} mono />
            <ListRow k="Exposed collateral" v={fmtUsd(ex.collateral_exposure_usd)} unknown={ex.collateral_exposure_usd === null} mono />
            {ex.positions.map((p) => (
              <div key={p.position_id} className="stack gap-4" style={{ padding: '8px 0', borderTop: '1px solid rgba(163,180,202,0.1)', fontSize: 11 }}>
                <span style={{ color: C.text }}>{r.positions.find((x) => x.id === p.position_id)?.title ?? p.position_id}</span>
                <span style={{ color: C.sec }}>Collateral {fmtUsd(p.collateral_value_usd)} · equity {fmtUsd(p.equity_usd)}</span>
                <span className="mono" style={{ fontSize: 10, color: C.unk }}>{p.path.map((x) => view.names.get(x)?.short ?? x).join(' → ')}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  )
}
