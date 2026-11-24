import { useSyncExternalStore } from 'react'
import type {
  EvidenceRecord,
  EvidenceSource,
  Health,
  IncidentSummary,
  IncidentTimeline,
  InvestigationRequest,
  LiveSources,
  ChatSessionData,
  ChatSessionSummary,
  ChatTurn,
  Job,
  JobCreated,
  Project,
  RunDetail,
  RunSummary,
} from './types'

// ------------------------------------------------------------------ backend mode
// 'api'      the BlackBox API answered GET /api/health
// 'fixture'  it did not; read-only views fall back to verbatim dev fixtures (banner shown)
export type BackendMode = 'checking' | 'api' | 'fixture'

interface BackendState {
  mode: BackendMode
  health: Health | null
  lastError: string | null
  checkedAt: number
}

let state: BackendState = { mode: 'checking', health: null, lastError: null, checkedAt: 0 }
const listeners = new Set<() => void>()

function setState(next: BackendState) {
  state = next
  listeners.forEach((l) => l())
}

export function useBackend(): BackendState {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => state,
  )
}

export async function checkHealth(): Promise<void> {
  try {
    const res = await fetch('/api/health', { headers: { Accept: 'application/json' } })
    if (!res.ok) throw new Error(`GET /api/health -> HTTP ${res.status}`)
    const health = (await res.json()) as Health
    setState({ mode: 'api', health, lastError: null, checkedAt: Date.now() })
  } catch (e) {
    setState({ mode: 'fixture', health: null, lastError: errText(e), checkedAt: Date.now() })
  }
}

let healthTimer: number | undefined
export function startHealthPolling(intervalMs = 10000) {
  if (healthTimer !== undefined) return
  void checkHealth()
  healthTimer = window.setInterval(() => void checkHealth(), intervalMs)
}

// ------------------------------------------------------------------ errors
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export class FixtureUnavailable extends Error {}

export function errText(e: unknown): string {
  if (e instanceof Error) return e.message
  return String(e)
}

async function readError(res: Response): Promise<string> {
  const text = await res.text()
  try {
    const body = JSON.parse(text) as { detail?: unknown; error?: unknown }
    const d = body.detail ?? body.error
    if (d !== undefined) return typeof d === 'string' ? d : JSON.stringify(d)
  } catch {
    /* not JSON */
  }
  return text.slice(0, 500) || res.statusText
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: 'application/json' } })
  if (!res.ok) throw new ApiError(res.status, `GET ${path} -> HTTP ${res.status}: ${await readError(res)}`)
  return (await res.json()) as T
}

const enc = encodeURIComponent

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new ApiError(res.status, `POST ${path} -> HTTP ${res.status}: ${await readError(res)}`)
  return (await res.json()) as T
}

// ------------------------------------------------------------------ endpoints
// Each read returns { data, fromFixture } so views can show the DEV FIXTURE banner.
export interface Loaded<T> {
  data: T
  fromFixture: boolean
}

async function withFallback<T>(apiCall: () => Promise<T>, fixture: () => Promise<T>): Promise<Loaded<T>> {
  if (state.mode === 'checking') await checkHealth()
  if (state.mode === 'api') return { data: await apiCall(), fromFixture: false }
  return { data: await fixture(), fromFixture: true }
}

function noFixture(what: string): () => Promise<never> {
  return () =>
    Promise.reject(
      new FixtureUnavailable(
        `The BlackBox API is not answering on 127.0.0.1:8700, and ${what} has no saved file to fall back to. Nothing is shown rather than an invented value.`,
      ),
    )
}

export const api = {
  project: () => withFallback(() => getJson<Project>('/api/project'), noFixture('project/connection status')),
  incidents: () =>
    withFallback(
      () => getJson<IncidentSummary[]>('/api/incidents'),
      async () => (await import('./fixtures')).fixtureIncidents(),
    ),
  runs: (batch?: string) =>
    withFallback(
      () => getJson<RunSummary[]>(`/api/runs${batch ? `?batch=${enc(batch)}` : ''}`),
      async () => {
        const rows = await (await import('./fixtures')).fixtureRuns()
        return batch ? rows.filter((r) => r.batch === batch) : rows
      },
    ),
  run: (runId: string) =>
    withFallback(
      () => getJson<RunDetail>(`/api/runs/${enc(runId)}`),
      async () => (await import('./fixtures')).fixtureRun(runId),
    ),
  /** Snapshot timeline read through the evidence backend (cutoff applied). No fixture: API only. */
  timeline: (incidentId: string) =>
    withFallback(() => getJson<IncidentTimeline>(`/api/incidents/${enc(incidentId)}/timeline`), noFixture('the incident timeline')),
  jobs: () => withFallback(() => getJson<Job[]>('/api/jobs'), noFixture('the job queue')),
  liveSources: () => withFallback(() => getJson<LiveSources>('/api/live/sources'), noFixture('live source status')),
  chatSessions: () => withFallback(() => getJson<ChatSessionSummary[]>('/api/chat/sessions'), noFixture('chat sessions')),
  chatSession: (id: string) => getJson<ChatSessionData>(`/api/chat/sessions/${enc(id)}`),
  chatStart: (body: { mode: 'live_stack' | 'snapshot'; alert?: string; incident_id?: string }) =>
    postJson<ChatSessionData>('/api/chat/sessions', body),
  chatAsk: (id: string, text: string) =>
    postJson<{ turn: ChatTurn; session: ChatSessionData }>(`/api/chat/sessions/${enc(id)}/messages`, { text }),
  job: (jobId: string) => getJson<Job>(`/api/jobs/${enc(jobId)}`),
  exportUrl: (runId: string) => `/api/runs/${enc(runId)}/export`,
  jobEventsUrl: (jobId: string) => `/api/jobs/${enc(jobId)}/events`,
  async cancelJob(jobId: string): Promise<Job> {
    return postJson<Job>(`/api/jobs/${enc(jobId)}/cancel`)
  },
  async retryJob(jobId: string): Promise<JobCreated> {
    return postJson<JobCreated>(`/api/jobs/${enc(jobId)}/retry`)
  },
  /** Original source of a record the run actually retrieved (404 for anything else). */
  evidenceSource: (runId: string, evidenceId: string) =>
    getJson<{ record: EvidenceRecord; source: EvidenceSource }>(
      `/api/runs/${enc(runId)}/evidence/${evidenceId.split('/').map(enc).join('/')}`,
    ),
  async startInvestigation(req: InvestigationRequest): Promise<JobCreated> {
    const res = await fetch('/api/investigations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(req),
    })
    if (!res.ok) throw new ApiError(res.status, `POST /api/investigations -> HTTP ${res.status}: ${await readError(res)}`)
    return (await res.json()) as JobCreated
  },
}

/** Download the raw output.json of a run (API export endpoint, or the verbatim fixture file). */
export async function downloadExport(runId: string, fromFixture: boolean): Promise<void> {
  let blob: Blob
  if (fromFixture) {
    blob = new Blob([await (await import('./fixtures')).fixtureExport(runId)], { type: 'application/json' })
  } else {
    const res = await fetch(api.exportUrl(runId))
    if (!res.ok) throw new ApiError(res.status, `GET export -> HTTP ${res.status}: ${await readError(res)}`)
    blob = await res.blob()
  }
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${runId.replace(/[\\/]/g, '__')}.output.json`
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
