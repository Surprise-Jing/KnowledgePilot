import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { type ApiError, toApiError } from '@/api/client'

export interface Resource<T> {
  data: T | undefined
  error: ApiError | null
  loading: boolean
  /** Reloads; `silent` keeps the current data on screen without a loading state (polling). */
  reload: (options?: { silent?: boolean }) => Promise<void>
  /** Optimistic local update of the loaded data. */
  mutate: (update: (current: T | undefined) => T | undefined) => void
}

interface State<T> {
  key: string | null
  data: T | undefined
  error: ApiError | null
  loading: boolean
}

/**
 * Loads data for `key` (null = nothing to load) and reloads when the key changes. Responses for
 * an older key or an older request are discarded, so fast switching never shows stale data.
 */
export function useResource<T>(key: string | null, loader: () => Promise<T>): Resource<T> {
  const loaderRef = useRef(loader)
  useLayoutEffect(() => {
    loaderRef.current = loader
  })
  const keyRef = useRef(key)
  const sequence = useRef(0)
  const [state, setState] = useState<State<T>>({
    key,
    data: undefined,
    error: null,
    loading: key !== null,
  })

  const load = useCallback(async (silent: boolean) => {
    const currentKey = keyRef.current
    if (currentKey === null) return
    const id = ++sequence.current
    if (!silent) {
      setState((s) => ({
        key: currentKey,
        data: s.key === currentKey ? s.data : undefined,
        error: null,
        loading: true,
      }))
    }
    try {
      const data = await loaderRef.current()
      if (id === sequence.current) setState({ key: currentKey, data, error: null, loading: false })
    } catch (error) {
      if (id === sequence.current) {
        setState((s) => ({
          key: currentKey,
          data: s.key === currentKey ? s.data : undefined,
          error: toApiError(error),
          loading: false,
        }))
      }
    }
  }, [])

  useEffect(() => {
    keyRef.current = key
    if (key === null) {
      // Nothing to load; the render-time key check already hides data of the previous key.
      sequence.current += 1
      return
    }
    void load(false)
    return () => {
      // Invalidate in-flight requests for the old key.
      sequence.current += 1
    }
  }, [key, load])

  const reload = useCallback((options?: { silent?: boolean }) => load(options?.silent ?? false), [load])
  const mutate = useCallback((update: (current: T | undefined) => T | undefined) => {
    setState((s) => ({ ...s, data: update(s.data) }))
  }, [])

  const current = state.key === key
  return {
    data: current ? state.data : undefined,
    error: current ? state.error : null,
    loading: current ? state.loading : key !== null,
    reload,
    mutate,
  }
}
