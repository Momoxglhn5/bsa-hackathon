// Request lifecycle for one on-demand call: ignores responses of superseded requests.
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, errorMessage, isAbort } from '../api/client'

export interface RequestState<T> {
  data: T | null
  error: string | null
  apiError: ApiError | null
  loading: boolean
  run: (fn: (signal: AbortSignal) => Promise<T>) => Promise<T | null>
  reset: () => void
}

export function useRequest<T>(): RequestState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [apiError, setApiError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(false)
  const ctrl = useRef<AbortController | null>(null)

  useEffect(() => () => ctrl.current?.abort(), [])

  const run = useCallback(async (fn: (signal: AbortSignal) => Promise<T>) => {
    ctrl.current?.abort()
    const c = new AbortController()
    ctrl.current = c
    setLoading(true)
    setError(null)
    setApiError(null)
    try {
      const res = await fn(c.signal)
      if (ctrl.current !== c) return null
      setData(res)
      return res
    } catch (err) {
      if (isAbort(err) || ctrl.current !== c) return null
      setError(errorMessage(err))
      setApiError(err instanceof ApiError ? err : null)
      return null
    } finally {
      if (ctrl.current === c) setLoading(false)
    }
  }, [])

  const reset = useCallback(() => {
    ctrl.current?.abort()
    ctrl.current = null
    setData(null)
    setError(null)
    setApiError(null)
    setLoading(false)
  }, [])

  return { data, error, apiError, loading, run, reset }
}

/** Load once per key (e.g. drawer content); re-runs when the key changes. */
export function useLoad<T>(key: string | null, fn: (signal: AbortSignal) => Promise<T>): RequestState<T> {
  const req = useRequest<T>()
  const fnRef = useRef(fn)
  fnRef.current = fn
  const { run, reset } = req
  useEffect(() => {
    if (key === null) { reset(); return }
    void run((s) => fnRef.current(s))
  }, [key, run, reset])
  return req
}
