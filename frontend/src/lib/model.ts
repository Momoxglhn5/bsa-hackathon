// View model built from backend objects: names, reachability and display heuristics.
// Nothing here produces a financial result; it only organises what the backend returned.
import type {
  AnalyticsCatalog, GraphDTO, GraphEdge, GraphNode, MarketDTO, NodeType, PositionDTO, QuoteContext,
  StrategyDTO, TokenRef, Valuation,
} from '../api/types'
import { fmtDate, fmtUsdCompact, num, shortAddr } from './format'
import { NODE_TYPE_LABEL } from './labels'

/** Canonical Ethereum mainnet token addresses, used only to name tokens without a symbol. */
const KNOWN_TOKENS: Record<string, string> = {
  '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48': 'USDC',
  '0xdac17f958d2ee523a2206206994597c13d831ec7': 'USDT',
  '0x6b175474e89094c44da98b954eedeac495271d0f': 'DAI',
  '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2': 'WETH',
}

export function tokenName(t: TokenRef | null | undefined): string {
  if (!t) return '—'
  return t.symbol || KNOWN_TOKENS[t.address.toLowerCase()] || shortAddr(t.address)
}

const assetId = (t: TokenRef) => `asset:${t.chain_id}:${t.address.toLowerCase()}`

export interface PositionView {
  id: string
  strategyId: string
  kind: PositionDTO['kind']
  dto: PositionDTO
  strategy: StrategyDTO
  morpho: MarketDTO | null
  pendle: MarketDTO | null
  quotes: QuoteContext[]
  /** Holder name, e.g. "Atlas Fund" or "Fund $2M". */
  title: string
  /** Market name, e.g. "PT-sUSDe → USDC". */
  pair: string
  ptName: string
  loanName: string
  maturity: string | null
}

/** PT symbol without its maturity suffix: "PT-sUSDe-26NOV2026" → "PT-sUSDe". */
export const ptShort = (symbol: string) => symbol.replace(/-\d{1,2}[A-Z]{3}\d{4}$/, '')

export function strategyPair(s: StrategyDTO): { pt: string; loan: string; pair: string } {
  const pt = s.pt.symbol ? ptShort(s.pt.symbol) : `PT ${shortAddr(s.pt.address)}`
  const loan = tokenName(s.loan_asset)
  return { pt, loan, pair: `${pt} → ${loan}` }
}

function positionTitle(p: PositionDTO): string {
  const label = p.label?.trim()
  // Data pipeline labels such as "Hypothetical 2000000 USD collateral; …" are descriptions, not names.
  if (label && label.length <= 40 && !/^hypothetical|^synthetic/i.test(label)) return label
  const size = /^pos_simulated_(\d+)usd$/.exec(p.position_id)
  if (size) return `Fund ${fmtUsdCompact(Number(size[1]))}`
  return p.kind === 'ONCHAIN' ? `Wallet ${shortAddr(p.owner ?? p.position_id)}` : p.position_id.replace(/^pos_/, '').replace(/_/g, ' ')
}

export function buildPositions(cat: AnalyticsCatalog): PositionView[] {
  return cat.positions.map((p) => {
    const strategy = cat.strategies.find((s) => s.strategy_id === p.strategy_id)!
    const morpho = cat.markets.find((m) => m.morpho && m.market_id === p.market_id) ?? null
    const pendle = cat.markets.find((m) => m.pendle && strategy && m.market_id === strategy.pendle_market_address) ?? null
    const names = strategy ? strategyPair(strategy) : { pt: tokenName(p.collateral_asset), loan: tokenName(p.loan_asset), pair: '' }
    return {
      id: p.position_id, strategyId: p.strategy_id, kind: p.kind, dto: p, strategy, morpho, pendle,
      quotes: cat.quotes.filter((q) => q.position_id === p.position_id),
      title: positionTitle(p), pair: names.pair, ptName: names.pt, loanName: names.loan,
      maturity: pendle?.pendle?.expiry ?? strategy?.expiry ?? null,
    }
  }).sort((a, b) => a.title.localeCompare(b.title, 'fr') || a.pair.localeCompare(b.pair, 'fr'))
}

// ---------------------------------------------------------------- graph helpers

export interface NodeInfo {
  node: GraphNode
  name: string
  short: string
  role: string
}

/** Token symbols known from every DTO of the catalog, keyed by graph asset id. */
function symbolIndex(cat: AnalyticsCatalog | null): Map<string, string> {
  const out = new Map<string, string>()
  const add = (t: TokenRef | null | undefined) => {
    if (t) {
      const name = t.symbol || KNOWN_TOKENS[t.address.toLowerCase()]
      if (name) out.set(assetId(t), name)
    }
  }
  for (const m of cat?.markets ?? []) {
    add(m.morpho?.loan_token); add(m.morpho?.collateral_token)
    add(m.pendle?.pt); add(m.pendle?.yt); add(m.pendle?.sy); add(m.pendle?.underlying_asset)
  }
  for (const s of cat?.strategies ?? []) { add(s.pt); add(s.loan_asset); add(s.underlying_asset) }
  return out
}

/** Readable names from the catalog DTOs; raw ids stay available in the details panel. */
export function nodeNames(graph: GraphDTO, positions: PositionView[], cat: AnalyticsCatalog | null): Map<string, NodeInfo> {
  const out = new Map<string, NodeInfo>()
  const symbols = symbolIndex(cat)
  const pairOf = new Map<string, string>()
  for (const s of cat?.strategies ?? []) {
    pairOf.set(`morpho:${s.chain_id}:${s.morpho_market_id}`, strategyPair(s).pair.replace(' → ', '/'))
    if (s.pendle_market_address) pairOf.set(`pendle:${s.chain_id}:${s.pendle_market_address}`, strategyPair(s).pt)
  }
  const oracleOf = new Map<string, string>()
  for (const m of cat?.markets ?? []) {
    if (m.morpho) oracleOf.set(`oracle:${m.chain_id}:${m.morpho.oracle_address.toLowerCase()}`, pairOf.get(`morpho:${m.chain_id}:${m.market_id}`) ?? '')
  }
  const incoming = (id: string) => graph.edges.filter((e) => e.target === id)
  for (const n of graph.nodes) {
    let name: string
    let short: string
    const addr = shortAddr(n.id)
    if (n.type === 'POSITION') {
      const pv = positions.find((p) => p.id === n.id)
      name = pv ? `${pv.title} · ${pv.pair}` : n.label
      short = pv ? pv.title : n.id
    } else if (symbols.has(n.id)) {
      name = symbols.get(n.id)!
      short = n.type === 'ASSET' && name.startsWith('PT-') ? ptShort(name) : name
    } else if (n.type === 'MARKET') {
      const kind = n.id.startsWith('morpho:') ? 'Morpho' : n.id.startsWith('pendle:') ? 'Pendle' : ''
      const pair = pairOf.get(n.id)
      name = `${kind} market${pair ? ` ${pair}` : ` ${addr}`}`.trim()
      short = `${kind} market`.trim()
    } else if (n.type === 'ORACLE') {
      const pair = oracleOf.get(n.id)
      name = `Oracle ${pair || addr}`
      short = 'Oracle'
    } else if (n.type === 'IRM') {
      name = 'Morpho rate model'
      short = 'IRM'
    } else {
      const rel = incoming(n.id)[0]?.relation_type
      const role = rel === 'BACKED_BY' ? 'Backing' : rel === 'ACCOUNTED_IN' ? 'Unit of account'
        : rel === 'USES_YIELD_TOKEN' ? 'Yield token' : rel === 'ACCEPTS_INPUT' ? 'SY input'
          : rel === 'DECLARES_REDEMPTION_TOKEN' ? 'SY output' : rel === 'DECLARES_ASSET' ? 'Declared asset' : NODE_TYPE_LABEL[n.type]
      name = `${role} ${addr}`
      short = role
    }
    out.set(n.id, { node: n, name, short, role: NODE_TYPE_LABEL[n.type] })
  }
  return out
}

/** Dependencies reachable from a node, following "source depends on target". */
export function reach(graph: Pick<GraphDTO, 'edges'>, from: string): Map<string, number> {
  const depth = new Map<string, number>([[from, 0]])
  let frontier = [from]
  let d = 0
  while (frontier.length) {
    d += 1
    const next: string[] = []
    for (const e of graph.edges) {
      if (frontier.includes(e.source) && !depth.has(e.target)) {
        depth.set(e.target, d)
        next.push(e.target)
      }
    }
    frontier = next
  }
  return depth
}

/** Edge between consecutive ids of a representative path, in either stored direction. */
export function edgeBetween(graph: GraphDTO, a: string, b: string): GraphEdge | undefined {
  return graph.edges.find((e) => e.source === a && e.target === b) ?? graph.edges.find((e) => e.source === b && e.target === a)
}

export type Vigilance = 'low' | 'med' | 'high'

/**
 * Visual vigilance level for graph colouring, from link evidence and structure only.
 * It is NOT the backend risk score and never feeds a calculation.
 */
export function vigilance(graph: GraphDTO, id: string, depth: number, sharedBy: number): Vigilance {
  const links = graph.edges.filter((e) => e.source === id || e.target === id)
  if (links.some((e) => e.verification_status === 'UNRESOLVED')) return 'high'
  const node = graph.nodes.find((n) => n.id === id)
  let score = 0
  if (node && (['ORACLE', 'MARKET', 'BRIDGE', 'GOVERNANCE'] as NodeType[]).includes(node.type)) score += 1
  if (depth === 1) score += 1
  if (sharedBy > 2) score += 1
  if (links.some((e) => e.required && e.verification_status === 'HYPOTHESIS')) score += 1
  if (links.some((e) => e.required && e.failure_modes.includes('VALUATION'))) score += 1
  return score >= 3 ? 'high' : score >= 2 ? 'med' : 'low'
}

export const VIGILANCE_COLOR: Record<Vigilance | 'pos', string> = { low: '#42C9A0', med: '#F2B45B', high: '#F17C82', pos: '#D4B47A' }
export const VIGILANCE_LABEL: Record<Vigilance, string> = { low: 'Low risk', med: 'Medium risk', high: 'High risk' }

export function maturityText(pv: PositionView, evaluatedAt: string | null): string {
  const expiry = pv.pendle?.pendle?.expiry ?? pv.strategy?.expiry ?? null
  if (!expiry) return 'PT maturity unknown'
  if (evaluatedAt && new Date(expiry) <= new Date(evaluatedAt)) return `PT matured on ${fmtDate(expiry)}`
  return `PT matures ${fmtDate(expiry)}`
}

/** LTV buffer in percentage points between oracle LTV and LLTV, from backend ratios. */
export function ltvBufferPts(v: Valuation | undefined): number | null {
  const ltv = num(v?.ltv_ratio)
  const lltv = num(v?.lltv_ratio)
  return ltv === null || lltv === null ? null : (lltv - ltv) * 100
}
