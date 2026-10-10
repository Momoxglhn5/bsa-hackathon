import { useEffect, useState } from 'react'
import { ApiError } from '../lib/api'

export type ApiResult<T> =
  | { state: 'loading' }
  | { state: 'error'; error: ApiError }
  | { state: 'ready'; value: T }

/** Fetch on demand (panel sections). `key` null skips the request. */
export function useApi<T>(key: string | null, request: (signal: AbortSignal) => Promise<T>): ApiResult<T> | null {
  const [result, setResult] = useState<{ key: string; value: ApiResult<T> } | null>(null)
  useEffect(() => {
    if (key === null) return
    const controller = new AbortController()
    setResult({ key, value: { state: 'loading' } })
    request(controller.signal).then(
      (value) => setResult({ key, value: { state: 'ready', value } }),
      (err: unknown) => {
        if (controller.signal.aborted) return
        const error = err instanceof ApiError ? err : new ApiError('error', String(err))
        setResult({ key, value: { state: 'error', error } })
      },
    )
    return () => controller.abort()
    // `request` is rebuilt every render; `key` identifies it.
  }, [key])
  if (key === null) return null
  return result?.key === key ? result.value : { state: 'loading' }
}
