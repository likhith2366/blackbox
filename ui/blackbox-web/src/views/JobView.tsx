import { useEffect, useMemo, useRef, useState } from 'react'
import { api, errText, useBackend } from '../api'
import { href, navigate } from '../hooks'
import { fmtNum, fmtTime } from '../format'
import { Badge, Card, Empty, ErrorBox, JobStatusBadge, KVTable, Loading } from '../components/ui'
import { EvidenceExplorer, EvidenceIndex, RunContext, buildIndex } from '../components/evidence'
import { MissingEvidence, ToolTimeline } from '../components/analysis'
import { Headline, ReportBody } from '../components/report'
import { Trace } from '../components/trace'
import type { EvidenceRecord, FinalReport, InvestigationStep, Job, RunDetail } from '../types'

const TERMINAL = new Set(['completed', 'failed', 'cancelled'])
const EVENT_TYPES = ['queued', 'waiting_gpu', 'started', 'round', 'report', 'completed', 'failed', 'cancel_requested', 'cancelled']

interface LoggedEvent {
  at: string // server event time `t` when present, else client receive time
  type: string
  summary: string
}

type Transport = 'connecting' | 'sse' | 'polling' | 'closed'

function isStep(x: unknown): x is InvestigationStep {
  return !!x && typeof x === 'object' && typeof (x as InvestigationStep).round === 'number' && 'stop_decision' in (x as object)
}
function isReport(x: unknown): x is FinalReport {
  return !!x && typeof x === 'object' && typeof (x as FinalReport).headline === 'string' && 'status' in (x as object)
}
/** `round {InvestigationStep}`: accept the step nested (step/data/round) or spread into the event. */
function extractStep(d: Record<string, unknown>): InvestigationStep | null {
  for (const k of ['step', 'data', 'round', 'investigation_step']) if (isStep(d[k])) return d[k] as InvestigationStep
  return isStep(d) ? d : null
}
function extractReport(d: Record<string, unknown>): FinalReport | null {
  for (const k of ['report', 'data']) if (isReport(d[k])) return d[k] as FinalReport
  return isReport(d) ? d : null
}

export function JobView({ jobId }: { jobId: string }) {
  const backend = useBackend()
  const [job, setJob] = useState<Job | null>(null)
  const [jobErr, setJobErr] = useState<string | null>(null)
  const [steps, setSteps] = useState<Map<number, InvestigationStep>>(new Map())
  const [report, setReport] = useState<FinalReport | null>(null)
  const [events, setEvents] = useState<LoggedEvent[]>([])
  const [transport, setTransport] = useState<Transport>('connecting')
  const [holder, setHolder] = useState<string | null>(null)
  const [run, setRun] = useState<RunDetail | null>(null)
  const [runErr, setRunErr] = useState<string | null>(null)
  const [actionErr, setActionErr] = useState<string | null>(null)
  const [acting, setActing] = useState(false)
  const terminalRef = useRef(false)

  // ---------------------------------------------------------------- live stream + polling fallback
  useEffect(() => {
    if (backend.mode !== 'api') return
    let cancelled = false
    let es: EventSource | null = null
    let pollTimer: number | undefined
    terminalRef.current = false

    let lastT: string | null = null
    const log = (type: string, summary: string) => {
      // capture the time now: the state updater runs later, after lastT has moved on
      const at = lastT ?? new Date().toISOString()
      setEvents((ev) => [...ev, { at, type, summary }])
    }

    const applyJob = (j: Job) => {
      if (cancelled) return
      setJob(j)
      setJobErr(null)
      // some servers may include rounds/report in the job document; use them if real data are there
      const maybeSteps = (j.steps ?? j.rounds) as unknown
      if (Array.isArray(maybeSteps)) {
        setSteps((prev) => {
          const m = new Map(prev)
          for (const s of maybeSteps) if (isStep(s)) m.set(s.round, s)
          return m
        })
      }
      if (isReport(j.report)) setReport(j.report)
      if (TERMINAL.has(j.status)) terminalRef.current = true
    }

    const poll = async () => {
      try {
        const j = await api.job(jobId)
        applyJob(j)
        if (TERMINAL.has(j.status)) {
          setTransport('closed')
          return
        }
      } catch (e) {
        if (!cancelled) setJobErr(errText(e))
      }
      if (!cancelled) pollTimer = window.setTimeout(poll, 2000)
    }

    const startPolling = () => {
      if (cancelled || terminalRef.current) return
      setTransport('polling')
      void poll()
    }

    const handle = (eventName: string, raw: string) => {
      let d: Record<string, unknown>
      try {
        d = JSON.parse(raw) as Record<string, unknown>
      } catch {
        log(eventName, `unparseable event data: ${raw.slice(0, 120)}`)
        return
      }
      const type = (typeof d.type === 'string' ? d.type : eventName) || 'message'
      lastT = typeof d.t === 'string' ? d.t : null
      setTransport('sse')
      switch (type) {
        case 'queued':
          setJob((j) => (j ? { ...j, status: 'queued', position: (d.position as number) ?? j.position } : j))
          log(type, d.position !== undefined ? `position ${String(d.position)}` : 'queued')
          break
        case 'waiting_gpu':
          setHolder((d.holder as string) ?? null)
          setJob((j) => (j ? { ...j, status: 'waiting_gpu' } : j))
          log(type, `GPU lock held by ${String(d.holder ?? 'unknown')}`)
          break
        case 'started':
          setJob((j) => (j ? { ...j, status: 'running' } : j))
          log(type, 'investigation started')
          break
        case 'round': {
          const s = extractStep(d)
          if (s) {
            setSteps((prev) => new Map(prev).set(s.round, s))
            log(type, `round ${s.round}: ${s.model_decision || '—'} · ${s.tool_requests?.length ?? 0} tool calls · ${s.stop_decision}`)
          } else log(type, 'round event without a recognisable InvestigationStep')
          break
        }
        case 'report': {
          const r = extractReport(d)
          if (r) setReport(r)
          log(type, r ? `${r.status}: ${r.headline}` : 'report event without a recognisable FinalReport')
          break
        }
        case 'completed':
          terminalRef.current = true
          setJob((j) => (j ? { ...j, status: 'completed', run_id: (d.run_id as string) ?? j.run_id } : j))
          log(type, `run ${String(d.run_id ?? '?')}`)
          es?.close()
          setTransport('closed')
          void api.job(jobId).then(applyJob, () => undefined)
          break
        case 'cancel_requested':
          setJob((j) => (j ? { ...j, status: 'cancel_requested' } : j))
          log(type, 'cancel requested; the run stops before its next round')
          break
        case 'cancelled':
          terminalRef.current = true
          setJob((j) => (j ? { ...j, status: 'cancelled' } : j))
          log(type, 'cancelled')
          es?.close()
          setTransport('closed')
          void api.job(jobId).then(applyJob, () => undefined)
          break
        case 'failed':
          terminalRef.current = true
          setJob((j) => (j ? { ...j, status: 'failed', error: (d.error as string) ?? j.error } : j))
          log(type, String(d.error ?? 'failed'))
          es?.close()
          setTransport('closed')
          void api.job(jobId).then(applyJob, () => undefined)
          break
        default:
          log(type, JSON.stringify(d).slice(0, 160))
      }
    }

    api.job(jobId).then(
      (j) => {
        applyJob(j)
        if (cancelled) return
        if (typeof EventSource === 'undefined') return startPolling()
        es = new EventSource(api.jobEventsUrl(jobId))
        es.onopen = () => !cancelled && setTransport('sse')
        es.onmessage = (m) => handle('message', m.data as string)
        for (const t of EVENT_TYPES) es.addEventListener(t, (m) => handle(t, (m as MessageEvent).data as string))
        es.onerror = () => {
          es?.close()
          if (cancelled) return
          if (terminalRef.current) setTransport('closed')
          else {
            log('stream', 'event stream closed or unavailable; falling back to polling GET /api/jobs/{id}')
            startPolling()
          }
        }
      },
      (e) => {
        if (!cancelled) {
          setJobErr(errText(e))
          setTransport('closed')
        }
      },
    )

    return () => {
      cancelled = true
      es?.close()
      if (pollTimer !== undefined) clearTimeout(pollTimer)
    }
  }, [jobId, backend.mode])

  // ---------------------------------------------------------------- saved run once completed
  const runId = job?.status === 'completed' ? job.run_id : null
  useEffect(() => {
    if (!runId) return
    api.run(runId).then(
      (r) => setRun(r.data),
      (e) => setRunErr(errText(e)),
    )
  }, [runId])

  const liveSteps = useMemo(() => [...steps.values()].sort((a, b) => a.round - b.round), [steps])
  const shownSteps = liveSteps.length ? liveSteps : (run?.output.steps ?? [])
  const shownReport = report ?? run?.output.report ?? null
  const index = useMemo(() => {
    const recs: EvidenceRecord[] = [...(run?.output.evidence_seen ?? [])]
    for (const s of shownSteps) for (const r of s.tool_results ?? []) recs.push(...r.records)
    return buildIndex(recs)
  }, [shownSteps, run])

  if (backend.mode === 'fixture')
    return (
      <div className="page">
        <ErrorBox error="The BlackBox API is not answering; live jobs cannot be shown (there is no fixture for a job)." />
      </div>
    )
  if (backend.mode === 'checking') return <Loading what="API status" />

  const lastCtx = liveSteps.length ? liveSteps[liveSteps.length - 1].context : null

  return (
    <RunContext.Provider value={job?.run_id ?? null}>
    <EvidenceIndex.Provider value={index}>
      <div className="page">
        <Card
          title={
            <>
              Job <code>{jobId}</code>
            </>
          }
          actions={
            <span className="small muted">
              updates:{' '}
              {transport === 'sse' ? (
                <Badge tone="info">live event stream</Badge>
              ) : transport === 'polling' ? (
                <Badge tone="warn">polling every 2 s</Badge>
              ) : transport === 'connecting' ? (
                <Badge tone="muted">connecting</Badge>
              ) : (
                <Badge tone="muted">closed</Badge>
              )}
            </span>
          }
        >
          {jobErr && <ErrorBox error={jobErr} />}
          {!job && !jobErr && <Loading what="job" />}
          {job && (
            <>
              <div className="job-status">
                <JobStatusBadge status={job.status} />
                <span>
                  {job.status === 'queued' &&
                    `Queued${job.position !== null && job.position !== undefined ? ` (position ${job.position})` : ''}. Waiting for earlier jobs.`}
                  {job.status === 'waiting_gpu' &&
                    `Waiting for the GPU lock${holder ? ` (held by ${holder})` : ''}. No inference starts while another job holds it.`}
                  {job.status === 'running' && `Running. ${liveSteps.length} round(s) received so far.`}
                  {job.status === 'cancel_requested' && 'Cancel requested: the run stops before its next round.'}
                  {job.status === 'completed' && 'Completed.'}
                  {job.status === 'failed' && 'Failed.'}
                  {job.status === 'cancelled' && 'Cancelled. No report is produced for a cancelled run.'}
                </span>
                {job.run_id && (
                  <a href={href('runs', job.run_id)}>
                    open saved run <code>{job.run_id}</code>
                  </a>
                )}
              </div>
              <div className="card-actions">
                {['queued', 'waiting_gpu', 'running'].includes(job.status) && (
                  <button
                    type="button"
                    className="danger"
                    disabled={acting}
                    onClick={() => {
                      setActing(true)
                      setActionErr(null)
                      api.cancelJob(jobId).then((j) => setJob((old) => ({ ...(old ?? j), ...j })), (e) => setActionErr(errText(e))).finally(() => setActing(false))
                    }}
                  >
                    Cancel job
                  </button>
                )}
                {['failed', 'cancelled'].includes(job.status) && (
                  <button
                    type="button"
                    className="primary"
                    disabled={acting}
                    onClick={() => {
                      setActing(true)
                      setActionErr(null)
                      api.retryJob(jobId).then((j) => navigate(href('jobs', j.job_id)), (e) => setActionErr(errText(e))).finally(() => setActing(false))
                    }}
                  >
                    Retry as new job
                  </button>
                )}
                {job.retry_of && (
                  <span className="small">
                    retry of <a href={href('jobs', job.retry_of)}>{job.retry_of}</a>
                  </span>
                )}
              </div>
              {actionErr && <ErrorBox error={actionErr} />}
              {job.error && <pre className="pre-wrap error-text">{job.error}</pre>}
              <div className="kv-inline small">
                <span>created {fmtTime(job.created)}</span>
                <span>started {fmtTime(job.started)}</span>
                <span>ended {fmtTime(job.ended)}</span>
              </div>
              {job.request && (
                <details className="small">
                  <summary>request</summary>
                  <KVTable data={job.request as Record<string, unknown>} />
                </details>
              )}
              {job.meta ? (
                <details className="small" open>
                  <summary>job metadata (mode, model settings, code revision, snapshot)</summary>
                  <KVTable data={job.meta as Record<string, unknown>} />
                </details>
              ) : (
                <p className="muted small">
                  {job.started || !TERMINAL.has(job.status)
                    ? 'Job metadata (model settings, code revision, snapshot) is filled in when the job starts.'
                    : 'No job metadata: this job never started.'}
                </p>
              )}
            </>
          )}
        </Card>

        {lastCtx && (
          <Card title="Context (latest round)">
            <div className="ctx-strip">
              <span>
                <span className="muted">prompt tokens est / actual</span>{' '}
                <strong>
                  {fmtNum(lastCtx.prompt_tokens_est)} / {fmtNum(lastCtx.prompt_tokens_actual)}
                </strong>
              </span>
              <span>
                <span className="muted">num_ctx</span> <strong>{fmtNum(lastCtx.num_ctx)}</strong>
              </span>
              <span>
                <span className="muted">notebook</span>{' '}
                <strong>
                  {fmtNum(lastCtx.notebook_tokens)} / {fmtNum(lastCtx.notebook_budget)}
                </strong>
              </span>
              <span>
                <span className="muted">records full / one-line / omitted</span>{' '}
                <strong>
                  {fmtNum(lastCtx.records_full)} / {fmtNum(lastCtx.records_one_line)} / {fmtNum(lastCtx.records_omitted)}
                </strong>
              </span>
            </div>
          </Card>
        )}

        <Card title={`Investigation rounds${liveSteps.length ? '' : run ? ' (from saved run)' : ''}`}>
          {shownSteps.length === 0 ? (
            <Empty>
              {job && !TERMINAL.has(job.status)
                ? 'No round has completed yet. Rounds appear here as the server reports them.'
                : 'No rounds were reported for this job.'}
            </Empty>
          ) : (
            <Trace steps={shownSteps} openLast />
          )}
        </Card>

        {shownSteps.length > 0 && (
          <>
            <Card title="Tool-call timeline">
              <ToolTimeline steps={shownSteps} />
            </Card>
            <Card title="Failures, refusals and missing evidence">
              <MissingEvidence steps={shownSteps} output={run?.output} />
            </Card>
            <Card title={`Evidence returned so far (${index.size})`}>
              <EvidenceExplorer records={[...index.values()]} />
            </Card>
          </>
        )}

        {shownReport && (
          <>
            <Card title="Final report">
              <Headline report={shownReport} />
            </Card>
            <ReportBody report={shownReport} steps={shownSteps} />
          </>
        )}
        {runErr && <ErrorBox error={`Could not load the saved run: ${runErr}`} />}

        <Card title={`Events received (${events.length})`}>
          {events.length === 0 ? (
            <Empty>No events received yet.</Empty>
          ) : (
            <ol className="events small">
              {events.map((e, i) => (
                <li key={i}>
                  <span className="muted">{fmtTime(e.at)}</span> <Badge tone="muted">{e.type}</Badge> {e.summary}
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>
    </EvidenceIndex.Provider>
    </RunContext.Provider>
  )
}
