import type { Market, Position, Relation, RelationStatus, RelationType, StrategyDetail, Token } from './types'

export type NodeKind = 'market' | 'pool' | 'asset' | 'oracle' | 'irm' | 'contract' | 'position'
/** RelationStatus plus the one link the UI joins itself (position → market). */
export type EdgeStatus = RelationStatus | 'UI_JOIN'
export type EdgeType = RelationType | 'POSITION_IN_MARKET'

export interface GraphNode {
  id: string
  label: string
  kind: NodeKind
  address: string | null
  /** Weakest verification status among the edges touching the node. */
  verification: EdgeStatus
  degree: number
}

export interface GraphEdge {
  id: string
  /** The dependent: `source` depends on `target`. */
  source: string
  target: string
  type: EdgeType
  status: EdgeStatus
  evidenceIds: string[]
  note: string | null
}

export interface Graph {
  nodes: GraphNode[]
  edges: GraphEdge[]
  byId: Map<string, GraphNode>
  outgoing: Map<string, GraphEdge[]>
  incoming: Map<string, GraphEdge[]>
}

export const STATUS_RANK: Record<EdgeStatus, number> = {
  ONCHAIN_VERIFIED: 0,
  API_OBSERVED: 1,
  CURATED: 2,
  UI_JOIN: 3,
  UNRESOLVED: 4,
}

export function shortAddress(address: string): string {
  return address.length > 12 ? `${address.slice(0, 6)}…${address.slice(-4)}` : address
}

function addressOf(nodeId: string): string | null {
  const last = nodeId.split(':').pop() ?? ''
  return last.startsWith('0x') ? last.toLowerCase() : null
}

function collectSymbols(markets: Market[], detail: StrategyDetail): Map<string, string> {
  const tokens: (Token | null | undefined)[] = [
    detail.strategy.pt,
    detail.strategy.loan_asset,
    detail.strategy.underlying_asset,
  ]
  for (const m of markets) {
    tokens.push(m.morpho?.loan_token, m.morpho?.collateral_token)
    tokens.push(m.pendle?.pt, m.pendle?.yt, m.pendle?.sy, m.pendle?.underlying_asset)
  }
  const symbols = new Map<string, string>()
  for (const t of tokens) if (t?.symbol) symbols.set(t.address.toLowerCase(), t.symbol)
  return symbols
}

function formatExpiry(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' })
}

function labelFor(id: string, kind: NodeKind, symbols: Map<string, string>, markets: Market[]): string {
  const address = addressOf(id)
  const market = markets.find((m) => `${m.protocol === 'MORPHO_BLUE' ? 'morpho' : 'pendle'}:1:${m.market_id.toLowerCase()}` === id)
  if (kind === 'market' && market?.morpho) {
    return `${market.morpho.collateral_token.symbol ?? '?'} / ${market.morpho.loan_token.symbol ?? '?'}`
  }
  if (kind === 'pool' && market?.pendle) {
    return `Pendle ${market.pendle.pt.symbol ?? 'PT'} · ${formatExpiry(market.pendle.expiry)}`
  }
  if (kind === 'position') return 'Position'
  const symbol = address ? symbols.get(address) : undefined
  if (symbol) return symbol
  const prefix = { oracle: 'Oracle', irm: 'Rate model', contract: 'Contract', asset: 'Asset' }[kind as string] ?? kind
  return address ? `${prefix} ${shortAddress(address)}` : id
}

function kindFor(id: string, roles: Map<string, NodeKind>): NodeKind {
  const role = roles.get(id)
  if (role) return role
  if (id.startsWith('morpho:')) return 'market'
  if (id.startsWith('pendle:')) return 'pool'
  if (id.startsWith('asset:')) return 'asset'
  if (id.startsWith('position:')) return 'position'
  return 'contract'
}

export function positionNodeId(position: Position): string {
  return `position:${position.position_id}`
}

/**
 * Build the dependency graph from GET /strategies/{id} relations. Node labels come from
 * the token symbols of the strategy and its markets. The position link is joined here:
 * it is not a backend RelationType.
 */
export function buildGraph(detail: StrategyDetail, markets: Market[], position: Position | null): Graph {
  const edges: GraphEdge[] = detail.relations.map((r: Relation) => ({
    id: r.relation_id,
    source: r.source_node_id.toLowerCase(),
    target: r.target_node_id.toLowerCase(),
    type: r.relation_type,
    status: r.verification_status,
    evidenceIds: r.evidence_ids,
    note: r.note,
  }))
  if (position && position.strategy_id === detail.strategy.strategy_id) {
    edges.push({
      id: `ui_join:${position.position_id}`,
      source: positionNodeId(position),
      target: `morpho:1:${position.market_id.toLowerCase()}`,
      type: 'POSITION_IN_MARKET',
      status: 'UI_JOIN',
      evidenceIds: [],
      note: 'Joined client-side from GET /positions/{id}; not a backend relation.',
    })
  }

  const roles = new Map<string, NodeKind>()
  for (const e of edges) {
    if (e.type === 'MARKET_ORACLE') roles.set(e.target, 'oracle')
    if (e.type === 'MARKET_IRM') roles.set(e.target, 'irm')
  }
  const symbols = collectSymbols(markets, detail)

  const byId = new Map<string, GraphNode>()
  const outgoing = new Map<string, GraphEdge[]>()
  const incoming = new Map<string, GraphEdge[]>()
  const touch = (id: string, edge: GraphEdge) => {
    let node = byId.get(id)
    if (!node) {
      const kind = kindFor(id, roles)
      node = {
        id,
        kind,
        label: labelFor(id, kind, symbols, markets),
        address: kind === 'position' ? null : addressOf(id),
        verification: edge.status,
        degree: 0,
      }
      byId.set(id, node)
      outgoing.set(id, [])
      incoming.set(id, [])
    }
    node.degree += 1
    // The UI-joined position link says nothing about how well the market is verified.
    const counts = edge.status !== 'UI_JOIN' || node.kind === 'position'
    if (counts && STATUS_RANK[edge.status] > STATUS_RANK[node.verification]) node.verification = edge.status
  }
  for (const e of edges) {
    touch(e.source, e)
    touch(e.target, e)
    outgoing.get(e.source)!.push(e)
    incoming.get(e.target)!.push(e)
  }
  return { nodes: [...byId.values()], edges, byId, outgoing, incoming }
}

/** Nodes within `depth` hops of `start`, ignoring direction. Value = distance. */
export function neighbourhood(graph: Graph, start: string, depth: number): Map<string, number> {
  const seen = new Map<string, number>([[start, 0]])
  let frontier = [start]
  for (let d = 1; d <= depth && frontier.length; d++) {
    const next: string[] = []
    for (const id of frontier) {
      for (const e of [...(graph.outgoing.get(id) ?? []), ...(graph.incoming.get(id) ?? [])]) {
        const other = e.source === id ? e.target : e.source
        if (!seen.has(other)) {
          seen.set(other, d)
          next.push(other)
        }
      }
    }
    frontier = next
  }
  return seen
}

/** Every edge reachable by following "depends on" arrows from `start`. */
export function upstreamEdges(graph: Graph, start: string): Set<string> {
  const found = new Set<string>()
  const stack = [start]
  const visited = new Set<string>()
  while (stack.length) {
    const id = stack.pop()!
    if (visited.has(id)) continue
    visited.add(id)
    for (const e of graph.outgoing.get(id) ?? []) {
      found.add(e.id)
      stack.push(e.target)
    }
  }
  return found
}

export interface PathStep {
  node: GraphNode
  /** Edge from this node to the next step (toward the position), if any. */
  via: GraphEdge | null
}

/** Nodes a shock on `id` reaches in one step, with the edge that carries it. */
function impactedBy(graph: Graph, id: string): { next: string; edge: GraphEdge }[] {
  // A dependency failing impairs whatever depends on it: walk against the arrows.
  const out = (graph.incoming.get(id) ?? []).map((edge) => ({ next: edge.source, edge }))
  // PENDLE_MARKET_PT is the one arrow walked forward: a PT redeems into the SY of its
  // market, so impairing the market's SY side impairs the PT.
  for (const edge of graph.outgoing.get(id) ?? []) {
    if (edge.type === 'PENDLE_MARKET_PT') out.push({ next: edge.target, edge })
  }
  return out
}

/** Shortest path a shock travels from `from` to `to`, or null when none exists. */
export function propagationPath(graph: Graph, from: string, to: string): PathStep[] | null {
  if (!graph.byId.has(from) || !graph.byId.has(to)) return null
  const prev = new Map<string, { id: string; edge: GraphEdge } | null>([[from, null]])
  const queue = [from]
  while (queue.length && !prev.has(to)) {
    const id = queue.shift()!
    for (const { next, edge } of impactedBy(graph, id)) {
      if (!prev.has(next)) {
        prev.set(next, { id, edge })
        queue.push(next)
      }
    }
  }
  if (!prev.has(to)) return null
  const steps: PathStep[] = []
  let cursor: string | null = to
  let via: GraphEdge | null = null
  while (cursor) {
    steps.unshift({ node: graph.byId.get(cursor)!, via })
    const step: { id: string; edge: GraphEdge } | null = prev.get(cursor) ?? null
    via = step ? step.edge : null
    cursor = step ? step.id : null
  }
  return steps
}
