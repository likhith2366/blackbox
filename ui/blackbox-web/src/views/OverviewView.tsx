import { api, useBackend } from '../api'
import { href, useLoad } from '../hooks'
import { fmtSeconds, fmtTime } from '../format'
import { Badge, Card, Empty, ErrorBox, FixtureBanner, JobStatusBadge, Loading, ReportStatusBadge, Stat } from '../components/ui'

const MODE_TONE = { live: 'info', snapshot: 'neutral', unavailable: 'muted' } as const

export function OverviewView() {
  const backend = useBackend()
  const h = backend.health
  const project = useLoad(() => api.project(), [])
  const runs = useLoad(() => api.runs(), [])
  const jobs = useLoad(() => api.jobs(), [])
  const incidents = useLoad(() => api.incidents(), [])

  return (
    <div className="page">
      {(runs.fromFixture || incidents.fromFixture) && <FixtureBanner />}
      <div className="ov-grid">
        <Card title="Model jobs">
          {backend.mode === 'checking' && <Loading what="API health" />}
          {backend.mode === 'fixture' && <ErrorBox error={`BlackBox API not reachable.\n${backend.lastError ?? ''}`} />}
          {backend.mode === 'api' && h && (
            <div className="stats">
              <Stat label="model" value={h.model ?? '—'} />
              <Stat label="GPU lock" value={h.gpu?.busy ? <Badge tone="warn">busy</Badge> : <Badge tone="good">free</Badge>} />
              <Stat label="queued" value={h.queue?.queued ?? '—'} />
              <Stat label="running" value={h.queue?.running ?? '—'} />
            </div>
          )}
          {h?.gpu?.busy && (
            <p className="small">
              GPU held by <code>{h.gpu.holder ?? 'unknown'}</code> since {fmtTime(h.gpu.since)}. Jobs wait until it is free.
            </p>
          )}
          <h4>Recent investigations</h4>
          {jobs.loading && <Loading what="jobs" />}
          {jobs.error && <p className="muted small">{jobs.error}</p>}
          {jobs.data && jobs.data.length === 0 && <Empty>No investigation started from this API session yet.</Empty>}
          {jobs.data && jobs.data.length > 0 && (
            <ul className="plain">
              {jobs.data.slice(0, 6).map((j) => (
                <li key={j.job_id}>
                  <JobStatusBadge status={j.status} /> <a href={href('jobs', j.job_id)}>{j.request?.incident_id ?? '?'}</a>{' '}
                  <span className="muted small">
                    {j.request?.mode} · created {fmtTime(j.created)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p>
            <a className="button" href={href('start')}>
              Start investigation
            </a>
          </p>
        </Card>

        <Card title="Connections" actions={<a href={href('connections')}>details</a>}>
          {project.loading && <Loading what="connection status" />}
          {project.error && <ErrorBox error={project.error} onRetry={project.reload} />}
          {project.data && (
            <>
              <p className="small">
                Repository <code>{project.data.repo ?? 'none'}</code>
              </p>
              <ul className="plain">
                {project.data.sources.map((s, i) => (
                  <li key={`${s.source}-${i}`}>
                    <Badge tone={MODE_TONE[s.mode as keyof typeof MODE_TONE] ?? 'neutral'}>{s.mode}</Badge> <strong>{s.source}</strong>{' '}
                    {s.error ? <span className="error-text small">{s.error}</span> : <span className="muted small">{s.detail}</span>}
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>

        <Card title="Incidents">
          {incidents.loading && <Loading what="incidents" />}
          {incidents.error && <ErrorBox error={incidents.error} onRetry={incidents.reload} />}
          {incidents.data && incidents.data.length === 0 && <Empty>No incident snapshots.</Empty>}
          {incidents.data && (
            <ul className="plain">
              {incidents.data.map((i) => (
                <li key={i.incident_id}>
                  <a href={href('incidents', i.incident_id)}>
                    <strong>{i.incident_id}</strong>
                  </a>{' '}
                  {i.alert.title} <span className="muted small">fired {fmtTime(i.alert.fired_at)}</span>{' '}
                  <a className="small" href={`${href('start')}?incident=${encodeURIComponent(i.incident_id)}`}>
                    investigate
                  </a>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Recent saved runs" actions={<a href={href('runs')}>all runs</a>}>
          {runs.loading && <Loading what="runs" />}
          {runs.error && <ErrorBox error={runs.error} onRetry={runs.reload} />}
          {runs.data && runs.data.length === 0 && <Empty>No saved runs.</Empty>}
          {runs.data && (
            <ul className="plain">
              {runs.data.slice(0, 8).map((r) => (
                <li key={r.run_id}>
                  <ReportStatusBadge status={r.report_status} /> <a href={href('runs', r.run_id)}>{r.run_id}</a>{' '}
                  <span className="muted small">
                    {r.system} · {fmtSeconds(r.wall_seconds)}
                    {r.status && r.status !== 'completed' ? ` · run ${r.status}` : ''}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )
}
