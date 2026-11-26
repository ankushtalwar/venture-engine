import { useCallback, useEffect, useRef, useState } from 'react'

export interface ApiState<T> {
  data: T | undefined
  error: Error | undefined
  loading: boolean
  reload: () => void
  setData: (next: T | ((prev: T | undefined) => T | undefined)) => void
}

/** Runs `load` on mount and whenever `key` changes. Ignores results from superseded calls. */
export function useApi<T>(load: () => Promise<T>, key: string): ApiState<T> {
  const [data, setData] = useState<T | undefined>(undefined)
  const [error, setError] = useState<Error | undefined>(undefined)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)
  const loadRef = useRef(load)
  loadRef.current = load

  useEffect(() => {
    let live = true
    setLoading(true)
    setError(undefined)
    loadRef
      .current()
      .then((value) => {
        if (live) setData(value)
      })
      .catch((err: unknown) => {
        if (live) setError(err instanceof Error ? err : new Error(String(err)))
      })
      .finally(() => {
        if (live) setLoading(false)
      })
    return () => {
      live = false
    }
  }, [key, tick])

  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { data, error, loading, reload, setData }
}

export function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}
