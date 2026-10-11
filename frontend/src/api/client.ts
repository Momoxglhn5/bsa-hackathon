// Single HTTP boundary to the backend: timeouts, cancellation and normalised errors.

export interface ApiSource {
  id: 'demo' | 'real'
  label: string
  base: string
  dataMode: 'REAL' | 'DEMO'
}

export const SOURCES: readonly ApiSource[] = [
  { id: 'demo', label: 'Demo', base: import.meta.env.VITE_DEMO_API_BASE ?? '/api-demo/v1', dataMode: 'DEMO' },
  { id: 'real', label: 'Live market', base: import.meta.env.VITE_API_BASE ?? '/api/v1', dataMode: 'REAL' },
]

export type FieldError = { loc: (string | number)[]; msg: string }

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail: string | FieldError[] | null

  constructor(status: number, code: string, detail: string | FieldError[] | null, message: string) {
    super(message)
    this.status = status
    this.code = code
    this.detail = detail
  }

  /** Errors attached to a body field, e.g. ['body', 'parameters', 'mint_amount']. */
  fieldErrors(): FieldError[] {
    return Array.isArray(this.detail) ? this.detail : []
  }
}

const DEFAULT_TIMEOUT_MS = 20_000

function describe(status: number, detail: string | FieldError[] | null): string {
  const text = Array.isArray(detail) ? detail.map((d) => `${d.loc.slice(1).join('.')} : ${d.msg}`).join(' · ') : detail
  switch (status) {
    case 0:
      return 'Server unreachable: check that the backend is running.'
    case 400:
      return `Parameter rejected by the backend${text ? ` (${text})` : ''}.`
    case 404:
      return `Not found in the served dataset${text ? ` (${text})` : ''}.`
    case 422:
      return `Validation failed${text ? `: ${text}` : ''}.`
    case 502:
    case 504:
      return 'Backend unreachable behind the dev proxy.'
    case 503:
      return `Data unavailable on the backend${text ? `: ${text}` : ''}.`
    default:
      return `Server error ${status}${text ? `: ${text}` : ''}.`
  }
}

export interface RequestOptions {
  signal?: AbortSignal
  timeoutMs?: number
  query?: Record<string, string | number | undefined | null>
  body?: unknown
}

export async function request<T>(source: ApiSource, method: 'GET' | 'POST', path: string, opts: RequestOptions = {}): Promise<T> {
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(opts.query ?? {})) {
    if (v !== undefined && v !== null && v !== '') params.set(k, String(v))
  }
  const qs = params.toString()
  const url = `${source.base}${path}${qs ? `?${qs}` : ''}`

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(new DOMException('timeout', 'TimeoutError')), opts.timeoutMs ?? DEFAULT_TIMEOUT_MS)
  const onAbort = () => controller.abort(opts.signal?.reason)
  opts.signal?.addEventListener('abort', onAbort, { once: true })

  let response: Response
  try {
    response = await fetch(url, {
      method,
      signal: controller.signal,
      headers: opts.body !== undefined ? { 'content-type': 'application/json', accept: 'application/json' } : { accept: 'application/json' },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    })
  } catch (err) {
    if (opts.signal?.aborted) throw err
    if (controller.signal.reason instanceof DOMException && controller.signal.reason.name === 'TimeoutError') {
      throw new ApiError(0, 'timeout', null, 'Request timed out: no response from the backend. Your inputs are kept.')
    }
    throw new ApiError(0, 'network', null, describe(0, null))
  } finally {
    clearTimeout(timeout)
    opts.signal?.removeEventListener('abort', onAbort)
  }

  const text = await response.text()
  let json: unknown = null
  try {
    json = text ? JSON.parse(text) : null
  } catch {
    json = null
  }
  if (!response.ok) {
    // The dev proxy answers an empty 500 when the backend process is not running.
    if (response.status >= 500 && text === '') {
      throw new ApiError(502, 'unreachable', null, 'Backend unreachable: no server answers behind the proxy. Check it runs on the right port.')
    }
    const body = (json ?? {}) as { error?: string; detail?: string | FieldError[] }
    const detail = body.detail ?? null
    throw new ApiError(response.status, body.error ?? 'error', detail, describe(response.status, detail))
  }
  if (json === null) throw new ApiError(response.status, 'invalid_json', null, 'Unreadable backend response.')
  return json as T
}

export function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return 'Unexpected error.'
}
