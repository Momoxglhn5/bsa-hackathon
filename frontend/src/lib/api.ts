import type {
  DataMode,
  DataStatusInfo,
  Envelope,
  Evidence,
  Market,
  Position,
  Series,
  Strategy,
  StrategyDetail,
} from './types'

const BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, '')
const TIMEOUT_MS = 10_000

export const DATA_MODE: DataMode = import.meta.env.VITE_DATA_MODE === 'REAL' ? 'REAL' : 'DEMO'
export const POSITION_ID: string = import.meta.env.VITE_POSITION_ID ?? 'pos_demo_ui_1'

export type ApiErrorKind = 'bad_request' | 'not_found' | 'unavailable' | 'network' | 'timeout' | 'error'

/** One error type for every failure, so screens can tell "absent" from "down". */
export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | null

  constructor(kind: ApiErrorKind, message: string, status: number | null = null) {
    super(message)
    this.kind = kind
    this.status = status
  }
}

function kindFor(status: number): ApiErrorKind {
  if (status === 400 || status === 422) return 'bad_request'
  if (status === 404) return 'not_found'
  if (status === 503) return 'unavailable'
  return 'error'
}

async function get<T>(path: string, params: Record<string, string> = {}, signal?: AbortSignal): Promise<Envelope<T>> {
  const query = new URLSearchParams({ ...params, data_mode: DATA_MODE })
  const timeout = AbortSignal.timeout(TIMEOUT_MS)
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout
  let res: Response
  try {
    res = await fetch(`${BASE}/api/v1${path}?${query}`, { signal: combined })
  } catch (err) {
    if (timeout.aborted) throw new ApiError('timeout', `API did not answer within ${TIMEOUT_MS / 1000} s`)
    if (signal?.aborted) throw err
    throw new ApiError('network', `API unreachable at ${BASE}`)
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Non-JSON error body: keep the status text.
    }
    throw new ApiError(kindFor(res.status), detail, res.status)
  }
  return (await res.json()) as Envelope<T>
}

export const api = {
  health: (s?: AbortSignal) => get<{ db: string; counts: Record<string, number> }>('/health', {}, s),
  strategies: (s?: AbortSignal) => get<Strategy[]>('/strategies', {}, s),
  strategy: (id: string, s?: AbortSignal) => get<StrategyDetail>(`/strategies/${encodeURIComponent(id)}`, {}, s),
  market: (id: string, s?: AbortSignal) => get<Market>(`/markets/${encodeURIComponent(id)}`, {}, s),
  position: (id: string, s?: AbortSignal) => get<Position>(`/positions/${encodeURIComponent(id)}`, {}, s),
  evidence: (id: string, s?: AbortSignal) => get<Evidence>(`/evidence/${encodeURIComponent(id)}`, {}, s),
  dataStatus: (entityId: string, s?: AbortSignal) => get<DataStatusInfo>('/data-status', { entity_id: entityId }, s),
  series: (entityId: string, metric: string, s?: AbortSignal) =>
    get<Series>('/series', { entity_id: entityId, metric }, s),
}
