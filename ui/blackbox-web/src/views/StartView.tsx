import { useEffect, useState, type FormEvent } from 'react'
import { api, errText, useBackend } from '../api'
import { href, navigate, useLoad, useRoute } from '../hooks'
import { fmtTime } from '../format'
import { Badge, Card, Empty, ErrorBox, FixtureBanner, JobStatusBadge, Loading } from '../components/ui'
import type { IncidentSummary, InvestigationRequest } from '../types'

// <input type="datetime-local"> has no zone; BlackBox times are UTC, so the inputs are UTC.
function toInput(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 23)
}
function fromInput(v: string): number {
  return new Date(v.endsWith('Z') ? v : v + 'Z').getTime()
}

export function StartView() {
  const backend = useBackend()
  const incidents = useLoad(() => api.incidents(), [])
  const { query } = useRoute()
  const followup = query.get('followup')
  const [incidentId, setIncidentId] = useState(query.get('incident') ?? '')
  const [mode, setMode] = useState<'snapshot' | 'live'>('snapshot')
  const [desc, setDesc] = useState(followup ? `Follow-up of saved run ${followup}: ` : '')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitErr, setSubmitErr] = useState<string | null>(null)

  const inc: IncidentSummary | undefined = incidents.data?.find((i) => i.incident_id === incidentId)

  useEffect(() => {
    if (!incidentId && incidents.data?.length) setIncidentId(incidents.data[0].incident_id)
  }, [incidents.data, incidentId])
  useEffect(() => {
    if (inc) {
      setStart(toInput(inc.window_start))
      setEnd(toInput(inc.window_end))
    }
  }, [inc])

  const wStart = inc ? new Date(inc.window_start).getTime() : 0
  const wEnd = inc ? new Date(inc.window_end).getTime() : 0
  const s = start ? fromInput(start) : NaN
  const e = end ? fromInput(end) : NaN
  let windowError: string | null = null
  if (inc) {
    if (Number.isNaN(s) || Number.isNaN(e)) windowError = 'Enter both window bounds.'
    else if (s < wStart || e > wEnd) windowError = 'The window must lie inside the incident window.'
    else if (s >= e) windowError = 'Window start must be before window end.'
  }
  const canSubmit = backend.mode === 'api' && !!inc && !windowError && !submitting

  async function submit(ev: FormEvent) {
    ev.preventDefault()
    if (!inc || !canSubmit) return
    const req: InvestigationRequest = { incident_id: inc.incident_id, mode }
    if (followup) req.followup_of = followup
    if (desc.trim()) req.problem_description = desc.trim()
    // send the window only when narrowed, so the default is exactly the incident window
    if (s !== wStart) req.window_start = new Date(s).toISOString()
    if (e !== wEnd) req.window_end = new Date(e).toISOString()
    setSubmitting(true)
    setSubmitErr(null)
    try {
      const job = await api.startInvestigation(req)
      navigate(href('jobs', job.job_id))
    } catch (err) {
      setSubmitErr(errText(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="page">
      {incidents.fromFixture && <FixtureBanner />}
      <Card title="Start an investigation">
        {backend.mode === 'fixture' && (
          <div className="callout callout-bad">
            The BlackBox API is not answering, so no investigation can be started. The incident list below comes from
            saved snapshot files.
          </div>
        )}
        {incidents.loading && <Loading what="incidents" />}
        {incidents.error && <ErrorBox error={incidents.error} onRetry={incidents.reload} />}
        {incidents.data && incidents.data.length === 0 && <Empty>No incidents available.</Empty>}
        {incidents.data && incidents.data.length > 0 && (
          <form className="form" onSubmit={submit}>
            <label>
              Incident
              <select value={incidentId} onChange={(ev) => setIncidentId(ev.target.value)}>
                {incidents.data.map((i) => (
                  <option key={i.incident_id} value={i.incident_id}>
                    {i.incident_id} — {i.alert.title}
                  </option>
                ))}
              </select>
            </label>

            {followup && (
              <div className="callout">
                Follow-up of saved run <a href={href('runs', followup)}>{followup}</a>. This starts a NEW investigation (new job and
                run id); the earlier run is unchanged. The link is sent as <code>followup_of</code> and stored with the job request.
              </div>
            )}
            {inc && (
              <div className="incident-box">
                <div>
                  <strong>{inc.alert.title}</strong> <Badge tone="muted">{inc.alert.alert_id}</Badge>
                </div>
                <div className="small">
                  {inc.alert.service} · <code>{inc.alert.condition}</code>
                  {inc.alert.observed_value !== null && inc.alert.observed_value !== undefined && ` · observed ${inc.alert.observed_value}`}{' '}
                  · fired {fmtTime(inc.alert.fired_at)}
                </div>
                <div className="small">
                  Incident window {fmtTime(inc.window_start)} → {fmtTime(inc.window_end)} · cutoff {fmtTime(inc.investigation_cutoff)}
                </div>
                <div className="small">
                  Services: {inc.services.join(', ')} · Sources: {inc.available_sources.join(', ')}
                </div>
              </div>
            )}

            <fieldset>
              <legend>Evidence mode</legend>
              <label className="inline">
                <input type="radio" name="mode" checked={mode === 'snapshot'} onChange={() => setMode('snapshot')} /> snapshot
                (frozen incident evidence)
              </label>
              <label className="inline">
                <input type="radio" name="mode" checked={mode === 'live'} onChange={() => setMode('live')} /> live (snapshot +
                live connectors; see Project)
              </label>
            </fieldset>

            <div className="row">
              <label>
                Window start (UTC)
                <input
                  type="datetime-local"
                  step="0.001"
                  value={start}
                  min={inc ? toInput(inc.window_start) : undefined}
                  max={inc ? toInput(inc.window_end) : undefined}
                  onChange={(ev) => setStart(ev.target.value)}
                />
              </label>
              <label>
                Window end (UTC)
                <input
                  type="datetime-local"
                  step="0.001"
                  value={end}
                  min={inc ? toInput(inc.window_start) : undefined}
                  max={inc ? toInput(inc.window_end) : undefined}
                  onChange={(ev) => setEnd(ev.target.value)}
                />
              </label>
            </div>
            {windowError && <div className="error-text">{windowError}</div>}

            <label>
              Problem description (optional; appended to the incident notes, marked as user-provided)
              <textarea rows={3} value={desc} onChange={(ev) => setDesc(ev.target.value)} maxLength={2000} />
            </label>

            <div>
              <button type="submit" className="primary" disabled={!canSubmit}>
                {submitting ? 'Submitting…' : 'Start investigation'}
              </button>
              <span className="muted small"> BlackBox system only. Jobs run one at a time when the GPU lock is free.</span>
            </div>
            {submitErr && <ErrorBox error={submitErr} />}
          </form>
        )}
      </Card>

      {backend.mode === 'api' && <JobsCard />}
    </div>
  )
}

function JobsCard() {
  const jobs = useLoad(() => api.jobs(), [])
  return (
    <Card
      title="Jobs"
      actions={
        <button type="button" onClick={jobs.reload}>
          Refresh
        </button>
      }
    >
      {jobs.loading && <Loading what="jobs" />}
      {jobs.error && <ErrorBox error={jobs.error} onRetry={jobs.reload} />}
      {jobs.data && jobs.data.length === 0 && <Empty>No jobs yet.</Empty>}
      {jobs.data && jobs.data.length > 0 && (
        <div className="table-scroll">
          <table className="grid">
            <thead>
              <tr>
                <th>job</th>
                <th>status</th>
                <th>incident</th>
                <th>mode</th>
                <th>created</th>
                <th>run</th>
              </tr>
            </thead>
            <tbody>
              {jobs.data.map((j) => (
                <tr key={j.job_id}>
                  <td>
                    <a href={href('jobs', j.job_id)}>
                      <code>{j.job_id}</code>
                    </a>
                  </td>
                  <td>
                    <JobStatusBadge status={j.status} />
                  </td>
                  <td>{j.request?.incident_id ?? '—'}</td>
                  <td>{j.request?.mode ?? '—'}</td>
                  <td>{fmtTime(j.created)}</td>
                  <td>{j.run_id ? <a href={href('runs', j.run_id)}>{j.run_id}</a> : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
