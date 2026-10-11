// Application state: selected dataset, loaded backend objects, selection, navigation and session results.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { SOURCES, errorMessage, isAbort, type ApiSource } from '../api/client'
import { getCatalog, getGraph, getRisk, getScenarios } from '../api/endpoints'
import type {
  AnalyticsCatalog, CoreEnvelope, DecisionResult, EvidenceDTO, GraphDTO, RiskResult, ScenarioScope, ScenarioType,
  SimulationRequest, SimulationResult,
} from '../api/types'
import { buildPositions, type PositionView } from '../lib/model'

export type Tab = 'explore' | 'simulate' | 'decide'
export type DrawerState =
  | { kind: 'data' }
  | { kind: 'method' }
  | { kind: 'incidents' }
  | { kind: 'evidence'; title: string; ids: string[] }
  | null

export interface SimRecord {
  result: SimulationResult
  request: SimulationRequest
  snapshotId: string
  sourceId: ApiSource['id']
  envelopeStatus: string
  envelopeWarnings: string[]
  targetName: string
  ranAt: number
}

export interface SimForm {
  type: ScenarioType
  target: string
  horizon: number
  scope: ScenarioScope
  oracle: number
  mintQty: string
  backing: string
  haircut: string
  ccHours: string
  ccService: 'redemption' | 'bridge_transfer'
  ccHaircut: string
  ccApply: boolean
}

export const DEFAULT_SIM_FORM: SimForm = {
  type: 'ORACLE_MANIPULATION', target: '', horizon: 7, scope: 'HYPOTHETICAL', oracle: -15,
  mintQty: '', backing: '', haircut: '20', ccHours: '24', ccService: 'redemption', ccHaircut: '', ccApply: false,
}

export type CarryMode = 'engine' | 'rates' | 'manual'

export interface DecisionForm {
  horizon: number
  mode: 'normal' | 'predictive'
  picks: Record<string, boolean>
  excludeUnquantified: Record<string, boolean>
  prob: Record<string, string>
  exclusivity: 'EXCLUSIVE' | 'OVERLAP' | 'UNKNOWN'
  sensitivityRef: string
  carryMode: CarryMode
  carryUsd: string
  carryWhy: string
  ptReturnPct: string
  borrowApyPct: string
  holdingFeesUsd: string
  ratesWhy: string
  quoteId: string
  useClosing: boolean
  closingFunds: string
  closingGas: string
  closingExtra: string
  closingFinancing: string
  closingWhy: string
}

export const DEFAULT_DECISION_FORM: DecisionForm = {
  horizon: 7, mode: 'normal', picks: {}, excludeUnquantified: {}, prob: {}, exclusivity: 'EXCLUSIVE', sensitivityRef: '',
  carryMode: 'engine', carryUsd: '', carryWhy: '', ptReturnPct: '', borrowApyPct: '', holdingFeesUsd: '0', ratesWhy: '',
  quoteId: '', useClosing: false, closingFunds: '', closingGas: '', closingExtra: '0', closingFinancing: '0', closingWhy: '',
}

export interface DecisionRecord {
  envelope: CoreEnvelope<DecisionResult>
  key: string
  snapshotId: string
}

interface Loaded {
  catalog: CoreEnvelope<AnalyticsCatalog> | null
  graphs: Record<string, CoreEnvelope<GraphDTO>>
  risks: Record<string, CoreEnvelope<RiskResult>>
  graphErrors: Record<string, string>
  riskErrors: Record<string, string>
  scenarios: ScenarioType[]
}

const EMPTY: Loaded = { catalog: null, graphs: {}, risks: {}, graphErrors: {}, riskErrors: {}, scenarios: [] }

interface RadarValue {
  source: ApiSource
  setSourceId: (id: ApiSource['id']) => void
  status: 'loading' | 'ready' | 'error'
  error: string | null
  loaded: Loaded
  reload: () => Promise<void>
  reloadedAt: number | null
  positions: PositionView[]
  sel: PositionView | null
  select: (positionId: string) => void
  graphFor: (strategyId: string) => GraphDTO | null
  evidence: Map<string, EvidenceDTO>
  snapshotId: string | null
  evaluatedAt: string | null
  // navigation
  tab: Tab
  setTab: (t: Tab) => void
  page: 'dash' | 'graph'
  setPage: (p: 'dash' | 'graph') => void
  pgAll: boolean
  setPgAll: (v: boolean) => void
  pgNode: string | null
  setPgNode: (id: string | null) => void
  pgPath: string[] | null
  showPath: (path: string[] | null) => void
  drawer: DrawerState
  setDrawer: (d: DrawerState) => void
  // session results
  simForm: SimForm
  setSimForm: (f: Partial<SimForm>) => void
  resetSimForm: () => void
  sims: SimRecord[]
  lastSim: SimRecord | null
  lastSimFormKey: string | null
  recordSim: (s: SimRecord, formKey: string) => void
  prefillShock: (nodeId: string, type: ScenarioType) => void
  decision: DecisionForm
  setDecision: (f: Partial<DecisionForm>) => void
  decisionResult: DecisionRecord | null
  setDecisionResult: (r: DecisionRecord | null) => void
}

const RadarContext = createContext<RadarValue | null>(null)

export function useRadar(): RadarValue {
  const v = useContext(RadarContext)
  if (!v) throw new Error('useRadar outside RadarProvider')
  return v
}

export function RadarProvider({ children }: { children: ReactNode }) {
  const [sourceId, setSourceIdState] = useState<ApiSource['id']>('demo')
  const source = SOURCES.find((s) => s.id === sourceId)!
  const [loaded, setLoaded] = useState<Loaded>(EMPTY)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [error, setError] = useState<string | null>(null)
  const [reloadedAt, setReloadedAt] = useState<number | null>(null)
  const [selId, setSelId] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('explore')
  const [page, setPage] = useState<'dash' | 'graph'>('dash')
  const [pgAll, setPgAll] = useState(false)
  const [pgNode, setPgNode] = useState<string | null>(null)
  const [pgPath, setPgPath] = useState<string[] | null>(null)
  const [drawer, setDrawer] = useState<DrawerState>(null)
  const [simForm, setSimFormState] = useState<SimForm>(DEFAULT_SIM_FORM)
  const [sims, setSims] = useState<SimRecord[]>([])
  const [lastSim, setLastSim] = useState<SimRecord | null>(null)
  const [lastSimFormKey, setLastSimFormKey] = useState<string | null>(null)
  const [decision, setDecisionState] = useState<DecisionForm>(DEFAULT_DECISION_FORM)
  const [decisionResult, setDecisionResult] = useState<DecisionRecord | null>(null)
  const loadSeq = useRef(0)
  const abortRef = useRef<AbortController | null>(null)

  const load = useCallback(async (src: ApiSource) => {
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    const seq = ++loadSeq.current
    setStatus('loading')
    setError(null)
    try {
      const [catalog, scenarios] = await Promise.all([
        getCatalog(src, { signal: ctrl.signal }),
        getScenarios(src, { signal: ctrl.signal }).catch(() => null),
      ])
      const next: Loaded = { ...EMPTY, catalog, scenarios: scenarios?.data.scenario_types ?? [], graphs: {}, risks: {}, graphErrors: {}, riskErrors: {} }
      await Promise.all(catalog.data.strategies.map(async (s) => {
        const [g, r] = await Promise.allSettled([getGraph(src, s.strategy_id, { signal: ctrl.signal }), getRisk(src, s.strategy_id, { signal: ctrl.signal })])
        if (g.status === 'fulfilled') next.graphs[s.strategy_id] = g.value
        else next.graphErrors[s.strategy_id] = errorMessage(g.reason)
        if (r.status === 'fulfilled') next.risks[s.strategy_id] = r.value
        else next.riskErrors[s.strategy_id] = errorMessage(r.reason)
      }))
      if (seq !== loadSeq.current) return
      setLoaded(next)
      setStatus('ready')
    } catch (err) {
      if (isAbort(err) || seq !== loadSeq.current) return
      setLoaded(EMPTY)
      setError(errorMessage(err))
      setStatus('error')
    }
  }, [])

  useEffect(() => {
    void load(source)
    return () => abortRef.current?.abort()
  }, [load, source])

  const positions = useMemo(() => (loaded.catalog ? buildPositions(loaded.catalog.data) : []), [loaded.catalog])
  const sel = positions.find((p) => p.id === selId) ?? positions[0] ?? null
  const snapshotId = loaded.catalog?.provenance.snapshot_id ?? null
  const evaluatedAt = loaded.catalog?.provenance.evaluated_at ?? null

  const evidence = useMemo(() => {
    const m = new Map<string, EvidenceDTO>()
    const envs = [loaded.catalog, ...Object.values(loaded.graphs), ...Object.values(loaded.risks)]
    for (const env of envs) for (const e of env?.provenance.evidence ?? []) m.set(e.evidence_id, e)
    return m
  }, [loaded])

  const clearPositionScoped = useCallback(() => {
    setPgNode(null)
    setPgPath(null)
    setSimFormState(DEFAULT_SIM_FORM)
    setLastSim(null)
    setLastSimFormKey(null)
    setDecisionResult(null)
    setDecisionState((d) => ({ ...DEFAULT_DECISION_FORM, horizon: d.horizon, mode: d.mode }))
  }, [])

  const currentSelId = sel?.id ?? null
  const select = useCallback((id: string) => {
    if (id !== currentSelId) clearPositionScoped()
    setSelId(id)
  }, [clearPositionScoped, currentSelId])

  const setSourceId = useCallback((id: ApiSource['id']) => {
    if (id === sourceId) return
    // A different dataset invalidates every id, simulation and decision of the previous one.
    setSelId(null)
    setSims([])
    setPgAll(false)
    clearPositionScoped()
    setSourceIdState(id)
  }, [clearPositionScoped, sourceId])

  const reload = useCallback(async () => {
    await load(source)
    setReloadedAt(Date.now())
  }, [load, source])

  // A new snapshot makes earlier simulation ids unusable for decisions.
  useEffect(() => {
    if (!snapshotId) return
    setSims((list) => list.filter((s) => s.snapshotId === snapshotId))
  }, [snapshotId])

  const graphFor = useCallback((sid: string) => loaded.graphs[sid]?.data ?? null, [loaded.graphs])

  const value: RadarValue = {
    source, setSourceId, status, error, loaded, reload, reloadedAt, positions, sel, select, graphFor, evidence,
    snapshotId, evaluatedAt,
    tab, setTab, page, setPage, pgAll, setPgAll, pgNode, setPgNode, pgPath, showPath: setPgPath, drawer, setDrawer,
    simForm,
    setSimForm: (f) => setSimFormState((s) => ({ ...s, ...f })),
    resetSimForm: () => { setSimFormState(DEFAULT_SIM_FORM); setLastSim(null); setLastSimFormKey(null) },
    sims, lastSim, lastSimFormKey,
    recordSim: (rec, key) => {
      setLastSim(rec)
      setLastSimFormKey(key)
      setSims((list) => [rec, ...list.filter((x) => x.result.simulation_id !== rec.result.simulation_id)].slice(0, 12))
    },
    prefillShock: (nodeId, type) => {
      setSimFormState((s) => ({ ...s, type, target: nodeId, scope: 'HYPOTHETICAL' }))
      setPage('dash')
      setTab('simulate')
      setDrawer(null)
    },
    decision,
    setDecision: (f) => setDecisionState((d) => ({ ...d, ...f })),
    decisionResult, setDecisionResult,
  }

  return <RadarContext.Provider value={value}>{children}</RadarContext.Provider>
}
