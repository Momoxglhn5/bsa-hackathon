import { createContext, useContext, useEffect, useState } from 'react'
import { ApiError, POSITION_ID, api } from '../lib/api'
import { buildGraph } from '../lib/graph'
import type { Graph } from '../lib/graph'
import type { Envelope, Market, Position, Series, Strategy, StrategyDetail } from '../lib/types'

export interface RadarData {
  strategies: Envelope<Strategy[]>
  detail: Envelope<StrategyDetail>
  markets: Envelope<Market>[]
  /** null when GET /positions/{id} answered 404: the screens then say so. */
  position: Envelope<Position> | null
  /** Collateral market price history, null when the API has none. */
  collateralPrice: Envelope<Series> | null
  graph: Graph
}

export type RadarState =
  | { state: 'loading' }
  | { state: 'error'; error: ApiError }
  | { state: 'ready'; data: RadarData }

async function optional<T>(request: Promise<T>): Promise<T | null> {
  try {
    return await request
  } catch (err) {
    if (err instanceof ApiError && err.kind === 'not_found') return null
    throw err
  }
}

async function load(strategyId: string | null, signal: AbortSignal): Promise<RadarData> {
  const strategies = await api.strategies(signal)
  const strategy = strategies.data.find((s) => s.strategy_id === strategyId) ?? strategies.data[0]
  if (!strategy) throw new ApiError('not_found', 'The API has no strategy to analyse.')
  const marketIds = [`morpho:1:${strategy.morpho_market_id}`]
  if (strategy.pendle_market_address) marketIds.push(`pendle:1:${strategy.pendle_market_address}`)
  const [detail, markets, position, collateralPrice] = await Promise.all([
    api.strategy(strategy.strategy_id, signal),
    Promise.all(marketIds.map((id) => api.market(id, signal))),
    optional(api.position(POSITION_ID, signal)),
    optional(api.series(`asset:1:${strategy.pt.address}`, 'MARKET_PRICE', signal)),
  ])
  const graph = buildGraph(
    detail.data,
    markets.map((m) => m.data),
    position?.data ?? null,
  )
  return { strategies, detail, markets, position, collateralPrice, graph }
}

export function useRadarLoader(strategyId: string | null, attempt: number): RadarState {
  const [result, setResult] = useState<RadarState>({ state: 'loading' })
  useEffect(() => {
    const controller = new AbortController()
    setResult({ state: 'loading' })
    load(strategyId, controller.signal).then(
      (data) => setResult({ state: 'ready', data }),
      (err: unknown) => {
        if (controller.signal.aborted) return
        const error = err instanceof ApiError ? err : new ApiError('error', String(err))
        setResult({ state: 'error', error })
      },
    )
    return () => controller.abort()
  }, [strategyId, attempt])
  return result
}

export const RadarContext = createContext<RadarData | null>(null)

export function useRadar(): RadarData {
  const data = useContext(RadarContext)
  if (!data) throw new Error('useRadar outside RadarContext')
  return data
}
