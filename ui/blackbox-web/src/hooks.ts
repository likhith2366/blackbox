import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'
import { errText, useBackend, type Loaded } from './api'

// ------------------------------------------------------------------ hash router
function subscribeHash(cb: () => void) {
  window.addEventListener('hashchange', cb)
  return () => window.removeEventListener('hashchange', cb)
}

export interface Route {
  path: string[] // decoded segments
  query: URLSearchParams
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribeHash, () => window.location.hash)
  const raw = hash.replace(/^#\/?/, '')
  const [p, q = ''] = raw.split('?')
  return {
    path: p.split('/').filter(Boolean).map(decodeURIComponent),
    query: new URLSearchParams(q),
  }
}

export function href(...segments: string[]): string {
  return '#/' + segments.map(encodeURIComponent).join('/')
}

export function navigate(to: string) {
  window.location.hash = to.replace(/^#/, '')
}

// ------------------------------------------------------------------ async loader
export interface LoadState<T> {
  loading: boolean
  error: string | null
  data: T | null
  fromFixture: boolean
  reload: () => void
}

/** Loads data and reloads when the backend switches between API and fixtures. */
export function useLoad<T>(fn: () => Promise<Loaded<T>>, deps: unknown[]): LoadState<T> {
  const { mode } = useBackend()
  const [tick, setTick] = useState(0)
  const [st, setSt] = useState<Omit<LoadState<T>, 'reload'>>({
    loading: true,
    error: null,
    data: null,
    fromFixture: false,
  })
  useEffect(() => {
    if (mode === 'checking') return
    let cancelled = false
    setSt((s) => ({ ...s, loading: true, error: null }))
    fn().then(
      (r) => !cancelled && setSt({ loading: false, error: null, data: r.data, fromFixture: r.fromFixture }),
      (e) => !cancelled && setSt({ loading: false, error: errText(e), data: null, fromFixture: false }),
    )
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, tick, ...deps])
  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { ...st, reload }
}
