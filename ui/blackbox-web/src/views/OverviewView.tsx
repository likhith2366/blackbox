import type { ReactNode } from 'react'
import { api, useBackend } from '../api'
import { href, useLoad } from '../hooks'
import { fmtSeconds, fmtTime } from '../format'
import { Badge, Card, Empty, ErrorBox, FixtureBanner, JobStatusBadge, Loading, ReportStatusBadge } from '../components/ui'

const MODE_TONE = { live: 'info', snapshot: 'neutral', unavailable: 'muted' } as const

function Kpi({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {hint && <div className="kpi-hint">{hint}</div>}
    </div>
  )
}

export function OverviewView() {
  const backend = useBackend()
  const h = backend.health
  const project = useLoad(() => api.project(), [])
  const runs = useLoad(() => api.runs(), [])
  const jobs = useLoad(() => api.jobs(), [])
  const incidents = useLoad(() => api.incidents(), [])

  const apiValue =
    backend.mode === 'api' ? <Badge tone="good">connected</Badge> : backend.mode === 'fixture' ? <Badge tone="bad">offline</Badge> : '…'
  const gpuValue = h?.gpu ? (h.gpu.busy ? <Badge tone="warn">busy</Badge> : <Badge tone="good">free</Badge>) : '—'

  return (
    <div className="page">
      {(runs.fromFixture || incidents.fromFixture) && <FixtureBanner />}

      <div className="kpis">
        <Kpi label="Incidents" value={incidents.data?.length ?? '—'} hint="snapshots available" />
        <Kpi label="Saved runs" value={runs.data?.length ?? '—'} hint="all systems and batches" />
        <Kpi label="BlackBox API" value={apiValue} hint={h?.model ? `model ${h.model}` : '127.0.0.1:8700'} />
        <Kpi
          label="GPU lock"
          value={gpuValue}
          hint={h?.queue ? `${h.queue.queued} queued · ${h.queue.running} running` : 'queue unknown'}
        />
      </div>

      <div className="overview-grid">
        <div className="overview-main">
          <Card title="Recent saved runs" actions={<a className="button small" href={href('runs')}>View all</a>}>
            {runs.loading && <Loading what="runs" />}
            {runs.error && <ErrorBox error={runs.error} onRetry={runs.reload} />}
            {runs.data && runs.data.length === 0 && <Empty>No saved runs.</Empty>}
            {runs.data && runs.data.length > 0 && (
              <div className="list">
                {runs.data.slice(0, 8).map((r) => (
                  <a className="list-row" key={r.run_id} href={href('runs', r.run_id)}>
                    <span className="list-main">
                      <span className="list-title mono">{r.run_id}</span>
                      <span className="list-meta">
                        {r.system} · {fmtSeconds(r.wall_seconds)}
                        {r.status && r.status !== 'completed' ? ` · run ${r.status}` : ''}
                      </span>
                    </span>
                    <ReportStatusBadge status={r.report_status} />
                  </a>
                ))}
              </div>
            )}
          </Card>

          <Card title="Investigation jobs" actions={<a className="button primary small" href={href('start')}>New investigation</a>}>
            {backend.mode === 'checking' && <Loading what="API health" />}
            {backend.mode === 'fixture' && (
              <ErrorBox error={`The BlackBox API is not reachable, so jobs cannot be listed. ${backend.lastError ?? ''}`} />
            )}
            {h?.gpu?.busy && (
              <p className="small muted">
                GPU held by <code>{h.gpu.holder ?? 'unknown'}</code> since {fmtTime(h.gpu.since)}. Jobs wait until it is free.
              </p>
            )}
            {jobs.loading && <Loading what="jobs" />}
            {backend.mode === 'api' && jobs.error && <p className="muted small">{jobs.error}</p>}
            {jobs.data && jobs.data.length === 0 && <Empty>No investigation started from this API session yet.</Empty>}
            {jobs.data && jobs.data.length > 0 && (
              <div className="list">
                {jobs.data.slice(0, 6).map((j) => (
                  <a className="list-row" key={j.job_id} href={href('jobs', j.job_id)}>
                    <span className="list-main">
                      <span className="list-title">{j.request?.incident_id ?? '?'}</span>
                      <span className="list-meta">
                        {j.request?.mode} · created {fmtTime(j.created)}
                      </span>
                    </span>
                    <JobStatusBadge status={j.status} />
                  </a>
                ))}
              </div>
            )}
          </Card>
        </div>

        <div className="overview-side">
          <Card title="Incidents">
            {incidents.loading && <Loading what="incidents" />}
            {incidents.error && <ErrorBox error={incidents.error} onRetry={incidents.reload} />}
            {incidents.data && incidents.data.length === 0 && <Empty>No incident snapshots.</Empty>}
            {incidents.data && incidents.data.length > 0 && (
              <div className="list">
                {incidents.data.map((i) => (
                  <div className="list-row" key={i.incident_id}>
                    <span className="list-main">
                      <a className="list-title" href={href('incidents', i.incident_id)}>
                        {i.incident_id}
                      </a>
                      <span className="list-meta">
                        {i.alert.title} · {fmtTime(i.alert.fired_at)}
                      </span>
                    </span>
                    <a className="button small" href={`${href('start')}?incident=${encodeURIComponent(i.incident_id)}`}>
                      Investigate
                    </a>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card title="Connections" actions={<a className="button small" href={href('connections')}>Details</a>}>
            {project.loading && <Loading what="connection status" />}
            {project.error && <ErrorBox error={project.error} onRetry={project.reload} />}
            {project.data && (
              <>
                <p className="small muted">
                  Repository <code>{project.data.repo ?? 'none'}</code>
                </p>
                <div className="list">
                  {project.data.sources.map((s, i) => (
                    <div className="list-row" key={`${s.source}-${i}`}>
                      <span className="list-main">
                        <span className="list-title">{s.source}</span>
                        {s.error ? (
                          <span className="list-meta error-text">{s.error}</span>
                        ) : (
                          <span className="list-meta">{s.detail}</span>
                        )}
                      </span>
                      <Badge tone={MODE_TONE[s.mode as keyof typeof MODE_TONE] ?? 'neutral'}>{s.mode}</Badge>
                    </div>
                  ))}
                </div>
              </>
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}
