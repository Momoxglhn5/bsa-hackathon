import { Background, ReactFlow, ReactFlowProvider, applyNodeChanges, useReactFlow, useStore } from '@xyflow/react'
import type { EdgeChange, NodeChange, NodeMouseHandler } from '@xyflow/react'
import { useCallback, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { useRadar } from '../data/useRadar'
import DependencyEdge from '../graph/DependencyEdge'
import type { DependencyFlowEdge, EdgeLook } from '../graph/DependencyEdge'
import InfoPanel from '../graph/InfoPanel'
import type { PanelTarget } from '../graph/InfoPanel'
import RadarNode from '../graph/RadarNode'
import type { RadarFlowNode } from '../graph/RadarNode'
import { neighbourhood, upstreamEdges } from '../lib/graph'
import type { Graph, GraphNode } from '../lib/graph'
import { forceLayout, nodeRadius } from '../lib/layout'
import { KIND, Segmented, VERIFICATION, VerificationBadge } from '../ui/primitives'

const nodeTypes = { radar: RadarNode }
const edgeTypes = { dependency: DependencyEdge }
const PANEL_W = 420
const HOVER_SCALE = 1.7
const SELECT_SCALE = 1.3

type ColorBy = 'kind' | 'verification'
type Mode = 'global' | 'local'

const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches
const duration = (ms: number) => (reducedMotion() ? 0 : ms)

function colorOf(node: GraphNode, by: ColorBy): string {
  return by === 'kind' ? KIND[node.kind].color : VERIFICATION[node.verification].color
}

function initialNodes(graph: Graph): RadarFlowNode[] {
  const layout = forceLayout(graph)
  return graph.nodes.map((n) => {
    const r = nodeRadius(n.kind, n.degree)
    const p = layout.get(n.id)!
    return {
      id: n.id,
      type: 'radar',
      position: { x: p.x - r, y: p.y - r },
      width: r * 2,
      height: r * 2,
      ariaLabel: `${n.label}, ${KIND[n.kind].label}, ${VERIFICATION[n.verification].label}`,
      data: { node: n, radius: r, color: '', scale: 1, opacity: 1, showLabel: true, emphasis: 'none' },
    }
  })
}

function Tooltip({ node, x, y }: { node: GraphNode; x: number; y: number }) {
  const { graph } = useRadar()
  const links = (graph.outgoing.get(node.id)?.length ?? 0) + (graph.incoming.get(node.id)?.length ?? 0)
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-20 max-w-[280px] rounded-md border border-[#2a3550] bg-[#101418] px-3 py-2.5 shadow-[0_8px_24px_rgba(0,0,0,.4)]"
      style={{ left: x + 14, top: y + 14 }}
    >
      <div className="flex items-center gap-1.5 text-[10px] text-ink-3">
        <span className="size-2 rounded-full" style={{ background: KIND[node.kind].color }} />
        {KIND[node.kind].label}
      </div>
      <div className="mt-1 font-medium">{node.label}</div>
      <div className="mt-1.5 flex items-center gap-3 text-[11px] text-ink-2">
        <span>{links} links</span>
        <VerificationBadge status={node.verification} />
      </div>
    </div>
  )
}

function ToolbarButton({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="inline-flex size-8 cursor-pointer items-center justify-center rounded-md border border-[#243049] bg-[rgba(15,21,36,.92)] text-ink-2 hover:bg-hover hover:text-ink"
    >
      {children}
    </button>
  )
}

function GraphCanvas() {
  const data = useRadar()
  const { graph } = data
  const flow = useReactFlow()
  const wrapper = useRef<HTMLDivElement>(null)

  const [baseNodes, setBaseNodes] = useState<RadarFlowNode[]>(() => initialNodes(graph))
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null)
  const [panel, setPanel] = useState<PanelTarget | null>(null)
  const [history, setHistory] = useState<PanelTarget[]>([])
  const [navEdge, setNavEdge] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>('global')
  const [depth, setDepth] = useState(2)
  const [colorBy, setColorBy] = useState<ColorBy>('kind')
  const [query, setQuery] = useState('')
  const [dragging, setDragging] = useState(false)

  const selectedNode = panel?.type === 'node' ? panel.id : null
  const selectedEdge = panel?.type === 'edge' ? panel.id : null
  const positionNode = graph.nodes.find((n) => n.kind === 'position')?.id ?? graph.nodes[0]?.id ?? null
  const localRoot = selectedNode ?? (selectedEdge ? (graph.edges.find((e) => e.id === selectedEdge)?.source ?? null) : positionNode)
  const localSet = useMemo(
    () => (mode === 'local' && localRoot ? neighbourhood(graph, localRoot, depth) : null),
    [graph, mode, localRoot, depth],
  )

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return []
    return graph.nodes.filter((n) => n.label.toLowerCase().includes(q) || n.id.includes(q))
  }, [graph, query])

  const centreOf = useCallback(
    (id: string) => {
      const n = baseNodes.find((b) => b.id === id)
      return n ? { x: n.position.x + n.data.radius, y: n.position.y + n.data.radius } : null
    },
    [baseNodes],
  )

  const focusOn = useCallback(
    (id: string) => {
      const c = centreOf(id)
      if (!c) return
      const zoom = Math.max(flow.getZoom(), 1.1)
      const width = wrapper.current?.offsetWidth ?? 1200
      // Keep the node clear of the info panel on wide screens.
      const shift = width > 760 ? PANEL_W / 2 / zoom : 0
      void flow.setCenter(c.x + shift, c.y, { zoom, duration: duration(480) })
    },
    [centreOf, flow],
  )

  const open = useCallback(
    (target: PanelTarget, opts: { via?: string; back?: boolean } = {}) => {
      if (panel && !opts.back && (panel.type !== target.type || panel.id !== target.id)) {
        setHistory((h) => [...h, panel].slice(-20))
      }
      setPanel(target)
      setNavEdge(opts.via ?? null)
      if (target.type === 'node') focusOn(target.id)
    },
    [focusOn, panel],
  )

  const back = () => {
    const prev = history.at(-1)
    if (!prev) return
    setHistory((h) => h.slice(0, -1))
    open(prev, { back: true })
  }

  const close = () => {
    setPanel(null)
    setNavEdge(null)
  }

  const fit = useCallback(
    (ids?: string[]) => {
      void flow.fitView({ nodes: ids?.map((id) => ({ id })), padding: 0.25, duration: duration(480), maxZoom: 2.2 })
    },
    [flow],
  )

  // Selection is ours (panel state). React Flow reports clicks and Enter/Space on a
  // focused element as `select` changes; positions and measurements are applied as-is.
  const onNodesChange = useCallback(
    (changes: NodeChange<RadarFlowNode>[]) => {
      const picked = changes.find((c) => c.type === 'select' && c.selected)
      if (picked && picked.type === 'select' && picked.id !== selectedNode) open({ type: 'node', id: picked.id })
      setBaseNodes((nds) => applyNodeChanges(changes.filter((c) => c.type !== 'select'), nds))
    },
    [open, selectedNode],
  )

  const onEdgesChange = useCallback(
    (changes: EdgeChange<DependencyFlowEdge>[]) => {
      const picked = changes.find((c) => c.type === 'select' && c.selected)
      if (picked && picked.type === 'select' && picked.id !== selectedEdge) open({ type: 'edge', id: picked.id })
    },
    [open, selectedEdge],
  )

  const toLocal = (e: { clientX: number; clientY: number }) => {
    const rect = wrapper.current?.getBoundingClientRect()
    return rect ? { x: e.clientX - rect.left, y: e.clientY - rect.top } : { x: 0, y: 0 }
  }
  const onNodeMouseEnter: NodeMouseHandler<RadarFlowNode> = (e, n) => setHover({ id: n.id, ...toLocal(e) })
  const onNodeMouseMove: NodeMouseHandler<RadarFlowNode> = (e, n) => setHover({ id: n.id, ...toLocal(e) })
  const onNodeMouseLeave = () => setHover(null)

  // ----- Visual state: derived every render from interaction state -----
  const hoverId = dragging ? null : (hover?.id ?? null)
  const focus = hoverId ?? selectedNode
  const lit = useMemo(() => {
    if (focus) return neighbourhood(graph, focus, 1)
    if (selectedEdge) {
      const e = graph.edges.find((x) => x.id === selectedEdge)
      return e ? new Map([[e.source, 0], [e.target, 0]]) : null
    }
    if (matches.length) return new Map(matches.map((n) => [n.id, 0]))
    return null
  }, [graph, focus, selectedEdge, matches])
  const upstream = useMemo(() => (selectedNode && !hoverId ? upstreamEdges(graph, selectedNode) : new Set<string>()), [graph, selectedNode, hoverId])
  const zoom = useStore((st) => st.transform[2])

  const nodes: RadarFlowNode[] = baseNodes.map((b) => {
    const n = b.data.node
    const isHover = hoverId === n.id
    const isSel = selectedNode === n.id
    const scale = isHover ? HOVER_SCALE : isSel ? SELECT_SCALE : 1
    const inLit = lit ? lit.has(n.id) : true
    const opacity = inLit ? 1 : hoverId ? 0.1 : 0.16
    // Small graphs show every label; large ones only hubs until zoomed in.
    const showLabel = lit ? inLit : graph.nodes.length <= 30 || n.kind === 'position' || n.degree >= 3 || zoom >= 1.35
    return {
      ...b,
      hidden: localSet ? !localSet.has(n.id) : false,
      selected: isSel,
      zIndex: isHover ? 3 : isSel ? 2 : 1,
      data: {
        ...b.data,
        color: colorOf(n, colorBy),
        scale,
        opacity,
        showLabel,
        emphasis: isHover ? 'hover' : isSel ? 'selected' : 'none',
      },
    }
  })

  const scaleOf = (id: string) => (hoverId === id ? HOVER_SCALE : selectedNode === id ? SELECT_SCALE : 1)
  const edges: DependencyFlowEdge[] = graph.edges.map((e) => {
    let look: EdgeLook = 'idle'
    if (focus) {
      if (e.source === focus) look = 'out'
      else if (e.target === focus) look = 'in'
      else if (upstream.has(e.id)) look = 'upstream'
      else if (lit?.has(e.source) && lit.has(e.target)) look = 'context'
      else look = 'faded'
    } else if (selectedEdge) look = e.id === selectedEdge ? 'selected' : 'faded'
    else if (lit) look = lit.has(e.source) || lit.has(e.target) ? 'context' : 'faded'
    const target = graph.byId.get(e.target)!
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      type: 'dependency',
      selected: e.id === selectedEdge,
      hidden: localSet ? !(localSet.has(e.source) && localSet.has(e.target)) : false,
      zIndex: look === 'faded' || look === 'idle' ? 0 : 1,
      ariaLabel: `${e.type}: ${graph.byId.get(e.source)!.label} depends on ${target.label}`,
      style: navEdge === e.id ? { filter: 'drop-shadow(0 0 6px #a5b4fc)' } : undefined,
      data: { edge: e, look, targetRadius: nodeRadius(target.kind, target.degree) * scaleOf(e.target) },
    }
  })

  const setModeAndFit = (m: Mode) => {
    setMode(m)
    const ids = m === 'local' && localRoot ? [...neighbourhood(graph, localRoot, depth).keys()] : undefined
    window.setTimeout(() => fit(ids), 0)
  }
  const setDepthAndFit = (d: number) => {
    setDepth(d)
    if (mode === 'local' && localRoot) window.setTimeout(() => fit([...neighbourhood(graph, localRoot, d).keys()]), 0)
  }

  const onSearchKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && matches[0]) {
      open({ type: 'node', id: matches[0].id })
      setQuery('')
    }
    if (e.key === 'Escape') setQuery('')
  }

  const visible = nodes.filter((n) => !n.hidden).length
  const legend =
    colorBy === 'kind'
      ? [...new Set(graph.nodes.map((n) => n.kind))].map((k) => ({ key: k, label: KIND[k].label, color: KIND[k].color, dashed: k === 'position' }))
      : [...new Set(graph.nodes.map((n) => n.verification))].map((s) => ({ key: s, label: VERIFICATION[s].label, color: VERIFICATION[s].color, dashed: false }))

  return (
    <div ref={wrapper} className="relative min-h-[560px] flex-1 overflow-hidden">
      {/* React Flow forces position: relative on its root, so the absolute box is a wrapper. */}
      <div className="absolute inset-0">
      <ReactFlow<RadarFlowNode, DependencyFlowEdge>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeMouseEnter={onNodeMouseEnter}
        onNodeMouseMove={onNodeMouseMove}
        onNodeMouseLeave={onNodeMouseLeave}
        onNodeDragStart={() => setDragging(true)}
        onNodeDragStop={() => setDragging(false)}
        onPaneClick={close}
        nodesConnectable={false}
        multiSelectionKeyCode={null}
        selectionKeyCode={null}
        deleteKeyCode={null}
        fitView
        fitViewOptions={{ padding: 0.25, maxZoom: 1.6 }}
        minZoom={0.25}
        maxZoom={3}
      >
        <Background color="#1b2438" gap={28} size={1} />
      </ReactFlow>
      </div>

      <div className="pointer-events-none absolute inset-x-3 top-3 z-10 flex flex-wrap items-start gap-2">
        <div className="pointer-events-auto relative">
          <label htmlFor="graph-search" className="sr-only">
            Search nodes
          </label>
          <input
            id="graph-search"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onSearchKey}
            placeholder="Search asset, market, oracle…"
            className="h-8 w-[240px] rounded-md border border-[#243049] bg-[rgba(15,21,36,.92)] px-2.5 text-xs text-ink placeholder:text-ink-4"
          />
          {query.trim() && (
            <ul className="absolute top-9 left-0 m-0 w-[280px] list-none overflow-hidden rounded-md border border-line bg-card p-0 shadow-lg">
              {matches.length ? (
                matches.map((n) => (
                  <li key={n.id}>
                    <button
                      type="button"
                      onClick={() => {
                        open({ type: 'node', id: n.id })
                        setQuery('')
                      }}
                      className="flex w-full cursor-pointer items-center gap-2 border-0 bg-transparent px-2.5 py-2 text-left text-xs hover:bg-hover"
                    >
                      <span className="size-2 rounded-full" style={{ background: KIND[n.kind].color }} />
                      <span className="flex-1 truncate">{n.label}</span>
                      <span className="text-[10px] text-ink-4">{KIND[n.kind].label}</span>
                    </button>
                  </li>
                ))
              ) : (
                <li className="px-2.5 py-2 text-xs text-ink-3">No node matches “{query.trim()}”.</li>
              )}
            </ul>
          )}
        </div>
        <div className="pointer-events-auto">
          <Segmented<Mode>
            label="Graph mode"
            value={mode}
            onChange={setModeAndFit}
            options={[
              { value: 'global', label: 'Global' },
              { value: 'local', label: 'Local' },
            ]}
          />
        </div>
        {mode === 'local' && (
          <div className="pointer-events-auto">
            <Segmented<number>
              label="Local depth"
              value={depth}
              onChange={setDepthAndFit}
              options={[1, 2, 3].map((d) => ({ value: d, label: `${d} hop${d > 1 ? 's' : ''}` }))}
            />
          </div>
        )}
        <div className="pointer-events-auto">
          <Segmented<ColorBy>
            label="Colour nodes by"
            value={colorBy}
            onChange={setColorBy}
            options={[
              { value: 'kind', label: 'Category' },
              { value: 'verification', label: 'Verification' },
            ]}
          />
        </div>
        <div className="pointer-events-auto flex gap-1.5">
          <ToolbarButton label="Zoom in" onClick={() => void flow.zoomIn({ duration: duration(200) })}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
          </ToolbarButton>
          <ToolbarButton label="Zoom out" onClick={() => void flow.zoomOut({ duration: duration(200) })}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M5 12h14" /></svg>
          </ToolbarButton>
          <ToolbarButton label="Fit graph to view" onClick={() => fit(localSet ? [...localSet.keys()] : undefined)}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /></svg>
          </ToolbarButton>
        </div>
      </div>

      <div className="pointer-events-none absolute bottom-3 left-3 z-10 flex flex-col gap-1.5 rounded-md border border-line bg-[rgba(15,21,36,.92)] px-3 py-2.5 text-[11px]">
        <div className="text-[10px] tracking-[.06em] text-ink-3 uppercase">{colorBy === 'kind' ? 'Node colour = category' : 'Node colour = weakest link status'}</div>
        {legend.map((l) => (
          <div key={l.key} className="flex items-center gap-2 text-ink-2">
            <span
              className="size-2.5 rounded-full"
              style={l.dashed ? { border: `1.5px dashed ${l.color}` } : { background: l.color }}
            />
            {l.label}
          </div>
        ))}
        <div className="mt-1 text-[10px] text-ink-4">Arrow: source depends on target</div>
      </div>

      <div
        className="pointer-events-none absolute bottom-3 z-10 text-right font-mono text-[10px] text-ink-4"
        style={{ right: panel ? PANEL_W + 16 : 16 }}
      >
        {visible} / {graph.nodes.length} nodes · {graph.edges.length} relations
      </div>

      {hover && !dragging && graph.byId.get(hover.id) && <Tooltip node={graph.byId.get(hover.id)!} x={hover.x} y={hover.y} />}

      {panel && (
        <InfoPanel
          target={panel}
          canGoBack={history.length > 0}
          onBack={back}
          onClose={close}
          onNavigate={(id, via) => open({ type: 'node', id }, { via })}
        />
      )}

      <p className="sr-only" aria-live="polite">
        {panel?.type === 'node' ? `Selected ${graph.byId.get(panel.id)?.label}` : ''}
      </p>
    </div>
  )
}

export default function GraphScreen() {
  const { graph } = useRadar()
  return (
    <ReactFlowProvider>
      {/* Remount on strategy change: the layout belongs to one graph. */}
      <GraphCanvas key={graph.nodes.map((n) => n.id).join('|')} />
    </ReactFlowProvider>
  )
}
