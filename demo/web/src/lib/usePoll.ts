import { useCallback, useEffect, useRef, useState } from 'react'
import type { HttpOutcome } from './http'

/** Calls `fn` now and every `intervalMs`; keeps the last outcome and a loading flag. */
export function usePoll(fn: () => Promise<HttpOutcome>, intervalMs: number) {
  const [outcome, setOutcome] = useState<HttpOutcome | null>(null)
  const [loading, setLoading] = useState(false)
  const fnRef = useRef(fn)
  useEffect(() => {
    fnRef.current = fn
  }, [fn])
  const alive = useRef(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    const o = await fnRef.current()
    if (alive.current) {
      setOutcome(o)
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    alive.current = true
    void refresh()
    const id = setInterval(() => void refresh(), intervalMs)
    return () => {
      alive.current = false
      clearInterval(id)
    }
  }, [refresh, intervalMs])

  return { outcome, loading, refresh }
}
