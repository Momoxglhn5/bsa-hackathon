import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { buildGraph, neighbourhood, positionNodeId, propagationPath, upstreamEdges } from './graph'
import type { Market, Position, Relation, Strategy } from './types'

// The backend's own DEMO fixture: the same objects GET /strategies/{id} etc. serve in DEMO mode.
const fixture = JSON.parse(
  readFileSync(new URL('../../../backend/app/fixtures/ui_mock_data_v1.json', import.meta.url), 'utf8'),
) as { markets: Market[]; strategies: Strategy[]; positions: Position[]; relations: Relation[] }

const strategy = fixture.strategies[0]!
const position = fixture.positions[0]!
const detail = { strategy, relations: fixture.relations }
const graph = buildGraph(detail, fixture.markets, position)

const MORPHO = `morpho:1:${strategy.morpho_market_id}`
const PENDLE = `pendle:1:${strategy.pendle_market_address}`
const ORACLE = 'contract:1:0x6666666666666666666666666666666666666666'
const BACKING = 'asset:1:0x8888888888888888888888888888888888888888'

describe('buildGraph', () => {
  it('adds one node per relation endpoint plus the joined position', () => {
    expect(graph.edges).toHaveLength(fixture.relations.length + 1)
    expect(graph.byId.get(positionNodeId(position))?.kind).toBe('position')
  })

  it('derives kinds from relation types and labels from token symbols', () => {
    expect(graph.byId.get(ORACLE)?.kind).toBe('oracle')
    expect(graph.byId.get(MORPHO)).toMatchObject({ kind: 'market', label: 'DEMO-PT-yUSD / DEMO-USD' })
    expect(graph.byId.get(PENDLE)?.kind).toBe('pool')
    expect(graph.byId.get('asset:1:0x2222222222222222222222222222222222222222')?.label).toBe('DEMO-PT-yUSD')
  })

  it('keeps the weakest verification status on each node', () => {
    expect(graph.byId.get(BACKING)?.verification).toBe('UNRESOLVED')
    expect(graph.byId.get(ORACLE)?.verification).toBe('CURATED')
    expect(graph.byId.get(MORPHO)?.verification).toBe('CURATED')
    expect(graph.byId.get(positionNodeId(position))?.verification).toBe('UI_JOIN')
  })

  it('does not join a position that belongs to another strategy', () => {
    const other = buildGraph(detail, fixture.markets, { ...position, strategy_id: 'strat_other' })
    expect(other.byId.has(positionNodeId(position))).toBe(false)
  })
})

describe('graph traversal', () => {
  it('returns the k-hop neighbourhood with distances', () => {
    const one = neighbourhood(graph, MORPHO, 1)
    expect(one.get(MORPHO)).toBe(0)
    expect(one.get(ORACLE)).toBe(1)
    expect(one.has(PENDLE)).toBe(false)
    expect(neighbourhood(graph, MORPHO, 2).get(PENDLE)).toBe(2)
  })

  it('follows dependency arrows upstream only', () => {
    const fromPosition = upstreamEdges(graph, positionNodeId(position))
    expect(fromPosition.size).toBe(5) // join + 4 Morpho market relations
    expect(upstreamEdges(graph, ORACLE).size).toBe(0)
  })

  it('finds the shock path from the oracle to the position', () => {
    const path = propagationPath(graph, ORACLE, positionNodeId(position))
    expect(path?.map((s) => s.node.id)).toEqual([ORACLE, MORPHO, positionNodeId(position)])
    expect(path?.map((s) => s.via?.type ?? null)).toEqual(['MARKET_ORACLE', 'POSITION_IN_MARKET', null])
  })

  it('reaches the position from the backing asset through PT redemption', () => {
    const path = propagationPath(graph, BACKING, positionNodeId(position))
    expect(path?.map((s) => s.via?.type ?? null)).toEqual([
      'SY_ACCOUNTING_ASSET',
      'PENDLE_MARKET_SY',
      'PENDLE_MARKET_PT',
      'MARKET_COLLATERAL_ASSET',
      'POSITION_IN_MARKET',
      null,
    ])
  })

  it('returns null when no path exists', () => {
    expect(propagationPath(graph, positionNodeId(position), ORACLE)).toBeNull()
  })
})
