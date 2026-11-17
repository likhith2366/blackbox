// One HTTP exchange, recorded exactly as observed in the browser.

export type OutcomeKind =
  | 'response' // the upstream service answered (any status code)
  | 'proxy-error' // the Vite dev proxy could not reach the upstream
  | 'client-timeout' // this page gave up waiting (AbortController)
  | 'network-error' // fetch itself failed (dev server down, etc.)

export interface HttpOutcome {
  kind: OutcomeKind
  method: string
  url: string
  requestBody?: unknown
  status?: number
  statusText?: string
  bodyText: string
  bodyJson?: unknown
  durationMs: number
  startedAt: string
  timeoutMs?: number
  errorMessage?: string
}

export function isOk(o: HttpOutcome): boolean {
  return o.kind === 'response' && o.status !== undefined && o.status >= 200 && o.status < 300
}

export async function httpCall(
  method: 'GET' | 'POST',
  url: string,
  body?: unknown,
  timeoutMs?: number,
): Promise<HttpOutcome> {
  const startedAt = new Date().toISOString()
  const ctrl = new AbortController()
  const timer = timeoutMs ? setTimeout(() => ctrl.abort(), timeoutMs) : undefined
  const t0 = performance.now()
  const base = { method, url, requestBody: body, startedAt, timeoutMs }
  try {
    const res = await fetch(url, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
      cache: 'no-store',
    })
    const bodyText = await res.text()
    const durationMs = performance.now() - t0
    let bodyJson: unknown
    try {
      bodyJson = bodyText ? JSON.parse(bodyText) : undefined
    } catch {
      bodyJson = undefined
    }
    return {
      ...base,
      kind: res.headers.get('X-Demo-Proxy-Error') ? 'proxy-error' : 'response',
      status: res.status,
      statusText: res.statusText,
      bodyText,
      bodyJson,
      durationMs,
    }
  } catch (e) {
    const durationMs = performance.now() - t0
    const aborted = ctrl.signal.aborted
    return {
      ...base,
      kind: aborted ? 'client-timeout' : 'network-error',
      bodyText: '',
      durationMs,
      errorMessage: aborted ? `no response within ${timeoutMs} ms` : String(e),
    }
  } finally {
    if (timer) clearTimeout(timer)
  }
}

export function fmtMs(ms: number): string {
  return ms >= 10000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`
}

/** FastAPI errors are {"detail": "..."}; checkout errors are {"error": "..."}. */
export function errorText(o: HttpOutcome): string | undefined {
  const j = o.bodyJson as Record<string, unknown> | undefined
  if (j && typeof j === 'object') {
    if (typeof j.detail === 'string') return j.detail
    if (typeof j.error === 'string') return j.error
    if (typeof j.proxy_error === 'string') return `${j.upstream}: ${j.proxy_error}`
  }
  return o.errorMessage
}
