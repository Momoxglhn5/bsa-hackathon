// One function per backend route (22 routes + /catalog). Paths are encoded, never rebuilt from labels.
import { request, type ApiSource } from './client'
import type {
  AnalyticsCatalog, CarryAssumptions, CarryResult, ClosingAssumptions, ClosingPlanResult, CoreEnvelope,
  DataEnvelope, DataStatusData, DecisionRequest, DecisionResult, DependencyExposure, EvidenceDTO, ExitQuoteRecord,
  GraphDTO, HealthData, IncidentCatalog, IncidentFrequencies, MarketDTO, MonteCarloRequest, MonteCarloResult,
  PositionDTO, RiskResult, ScenarioCatalog, SimulationRequest, SimulationResult, StrategyDTO, TimeseriesDTO,
} from './types'

type Sig = { signal?: AbortSignal }
const e = encodeURIComponent
const mode = (s: ApiSource) => ({ data_mode: s.dataMode })

// ---------------------------------------------------------------- Core (analytics)

export const getCatalog = (s: ApiSource, o: Sig = {}) =>
  request<CoreEnvelope<AnalyticsCatalog>>(s, 'GET', '/catalog', { ...o, query: mode(s) })

export const getGraph = (s: ApiSource, strategyId: string, o: Sig = {}) =>
  request<CoreEnvelope<GraphDTO>>(s, 'GET', `/graph/${e(strategyId)}`, { ...o, query: mode(s) })

export const getExposure = (s: ApiSource, strategyId: string, nodeId: string, o: Sig = {}) =>
  request<CoreEnvelope<DependencyExposure>>(s, 'GET', `/graph/${e(strategyId)}/exposure`, { ...o, query: { ...mode(s), node_id: nodeId } })

export const getRisk = (s: ApiSource, strategyId: string, o: Sig = {}) =>
  request<CoreEnvelope<RiskResult>>(s, 'GET', `/risk/${e(strategyId)}`, { ...o, query: mode(s) })

export const getScenarios = (s: ApiSource, o: Sig = {}) =>
  request<CoreEnvelope<ScenarioCatalog>>(s, 'GET', '/scenarios', o)

export const getIncidentFrequencies = (s: ApiSource, o: Sig = {}) =>
  request<CoreEnvelope<IncidentFrequencies>>(s, 'GET', '/incident-frequencies', o)

export const postSimulation = (s: ApiSource, body: SimulationRequest, o: Sig = {}) =>
  request<CoreEnvelope<SimulationResult>>(s, 'POST', '/simulations', { ...o, query: mode(s), body })

export const postMonteCarlo = (s: ApiSource, body: MonteCarloRequest, o: Sig = {}) =>
  request<CoreEnvelope<MonteCarloResult>>(s, 'POST', '/monte-carlo', { ...o, query: mode(s), body, timeoutMs: 90_000 })

export const postCarry = (s: ApiSource, body: { strategy_id: string; position_id: string; horizon_days: number; assumptions?: CarryAssumptions | null }, o: Sig = {}) =>
  request<CoreEnvelope<CarryResult>>(s, 'POST', '/carry-estimates', { ...o, query: mode(s), body })

export const postClosingPlan = (s: ApiSource, body: { strategy_id: string; position_id: string; exit_quote_id: string | null; assumptions?: ClosingAssumptions | null }, o: Sig = {}) =>
  request<CoreEnvelope<ClosingPlanResult>>(s, 'POST', '/closing-plans', { ...o, query: mode(s), body })

export const postDecision = (s: ApiSource, body: DecisionRequest, o: Sig = {}) =>
  request<CoreEnvelope<DecisionResult>>(s, 'POST', '/decisions', { ...o, query: mode(s), body })

// ---------------------------------------------------------------- Data V1 (stores)

export const getHealth = (s: ApiSource, o: Sig = {}) =>
  request<DataEnvelope<HealthData>>(s, 'GET', '/health', { ...o, query: mode(s) })

export const getMarkets = (s: ApiSource, o: Sig = {}) =>
  request<DataEnvelope<MarketDTO[]>>(s, 'GET', '/markets', { ...o, query: { ...mode(s), chain_id: 1 } })

export const getMarket = (s: ApiSource, marketKey: string, o: Sig = {}) =>
  request<DataEnvelope<MarketDTO>>(s, 'GET', `/markets/${e(marketKey)}`, { ...o, query: mode(s) })

export const getStrategies = (s: ApiSource, o: Sig = {}) =>
  request<DataEnvelope<StrategyDTO[]>>(s, 'GET', '/strategies', { ...o, query: mode(s) })

export const getStrategy = (s: ApiSource, strategyId: string, o: Sig = {}) =>
  request<DataEnvelope<{ strategy: StrategyDTO; relations: unknown[] }>>(s, 'GET', `/strategies/${e(strategyId)}`, { ...o, query: mode(s) })

export const getSeries = (s: ApiSource, entityId: string, metric: string, o: Sig = {}) =>
  request<DataEnvelope<TimeseriesDTO>>(s, 'GET', '/series', { ...o, query: { ...mode(s), entity_id: entityId, metric } })

export const getPosition = (s: ApiSource, positionId: string, o: Sig = {}) =>
  request<DataEnvelope<PositionDTO>>(s, 'GET', `/positions/${e(positionId)}`, { ...o, query: mode(s) })

export const getDataStatus = (s: ApiSource, entityId: string, o: Sig = {}) =>
  request<DataEnvelope<DataStatusData>>(s, 'GET', '/data-status', { ...o, query: { ...mode(s), entity_id: entityId } })

export const getEvidence = (s: ApiSource, evidenceId: string, o: Sig = {}) =>
  request<DataEnvelope<EvidenceDTO>>(s, 'GET', `/evidence/${e(evidenceId)}`, { ...o, query: mode(s) })

// ---------------------------------------------------------------- Delivery (REAL cache, never a live provider call)

export const getDataDelivery = (s: ApiSource, o: Sig = {}) =>
  request<DataEnvelope<Record<string, unknown>>>(s, 'GET', '/data-delivery', o)

export const postExitQuote = (s: ApiSource, body: { strategy_id: string; input_amount: string }, o: Sig = {}) =>
  request<DataEnvelope<ExitQuoteRecord>>(s, 'POST', '/exit-quotes', { ...o, body })

export const getIncidentCatalog = (s: ApiSource, o: Sig = {}) =>
  request<DataEnvelope<IncidentCatalog>>(s, 'GET', '/incident-catalog', o)
