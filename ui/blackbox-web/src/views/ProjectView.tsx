import { api, useBackend } from '../api'
import { useLoad } from '../hooks'
import { fmtTime } from '../format'
import { Badge, Card, Empty, ErrorBox, Loading, Stat } from '../components/ui'

const MODE_TONE = { live: 'info', snapshot: 'neutral', unavailable: 'muted' } as const

/** What to check for an unavailable/unhealthy source. Derived only from the reported error/detail text. */
function diagnose(mode: string, error?: string | null, detail?: string | null): string | null {
  const e = `${error ?? ''} ${detail ?? ''}`.toLowerCase()
  if (mode !== 'unavailable' && !error) return null
  if (e.includes('not configured')) return 'No credentials or site are configured on this machine; nothing to fix unless the integration is wanted.'
  if (e.includes('rate limit')) return 'GitHub rate limit exhausted: wait for the reset time in the error, or authenticate (GITHUB_TOKEN or gh auth login).'
  if (e.includes('http 401') || e.includes('bad credentials')) return 'The token was rejected: check GITHUB_TOKEN or run gh auth login.'
  if (e.includes('http 403')) return 'Access denied: the token lacks access to this repository.'
  if (e.includes('http 404')) return 'Repository (or object) not found, or private without access.'
  if (e.includes('http 409') || e.includes('empty')) return 'The repository is reachable but has no pushed commits yet.'
  if (e.includes('no log directory') || e.includes('no log files')) return 'No log files at the telemetry directory: start the demo services or set BLACKBOX_TELEMETRY_DIR.'
  if (e.includes('timed out') || e.includes('network')) return 'Network failure or timeout reaching the source: check connectivity and retry.'
  if (error) return 'See the error text; the source answered with a failure.'
  return null
}

export function ProjectView() {
  const backend = useBackend()
  const st = useLoad(() => api.project(), [])
  const live = useLoad(() => api.liveSources(), [])
  const h = backend.health

  return (
    <div className="page">
      <Card title="BlackBox API">
        {backend.mode === 'checking' && <Loading what="API health" />}
        {backend.mode === 'fixture' && (
          <ErrorBox error={`BlackBox API not reachable at 127.0.0.1:8700 (via the /api proxy).\n${backend.lastError ?? ''}`} />
        )}
        {backend.mode === 'api' && h && (
          <div className="stats">
            <Stat label="API" value={h.ok ? <Badge tone="good">ok</Badge> : <Badge tone="bad">not ok</Badge>} />
            <Stat label="model" value={h.model ?? '—'} />
            <Stat
              label="GPU lock"
              value={h.gpu?.busy ? <Badge tone="warn">busy</Badge> : <Badge tone="good">free</Badge>}
              hint={h.gpu?.busy ? `holder ${h.gpu?.holder ?? '?'} since ${h.gpu?.since ?? '?'}` : undefined}
            />
            {h.gpu?.busy && <Stat label="GPU holder" value={`${h.gpu.holder ?? '?'} since ${fmtTime(h.gpu.since)}`} />}
            <Stat label="jobs queued" value={h.queue?.queued ?? '—'} />
            <Stat label="jobs running" value={h.queue?.running ?? '—'} />
          </div>
        )}
      </Card>

      <Card
        title="Live stack sources"
        actions={
          <button type="button" onClick={live.reload}>
            Re-check
          </button>
        }
      >
        <p className="small muted">
          Sources a live-stack investigation reads. Each is queried directly; an unavailable or unconfigured source is
          reported as such and never replaced by snapshot data.
        </p>
        {live.loading && <Loading what="live source status" />}
        {live.error && <ErrorBox error={live.error} onRetry={live.reload} />}
        {live.data && (
          <div className="list">
            {live.data.sources.map((s) => (
              <div className="list-row" key={s.source}>
                <span className="list-main">
                  <span className="list-title">{s.source}</span>
                  <span className="list-meta">{s.detail}</span>
                </span>
                <Badge
                  tone={s.status === 'live' ? 'good' : s.status === 'not configured' ? 'muted' : s.status === 'error' ? 'bad' : 'warn'}
                >
                  {s.status}
                </Badge>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card
        title="Project & evidence connections"
        actions={
          <button type="button" onClick={st.reload}>
            Re-check
          </button>
        }
      >
        {st.loading && <Loading what="project status" />}
        {st.error && <ErrorBox error={st.error} onRetry={st.reload} />}
        {st.data && (
          <>
            <p>
              Configured repository: {st.data.repo ? <code>{st.data.repo}</code> : <span className="muted">none configured</span>}
            </p>
            {st.data.sources.length === 0 ? (
              <Empty>No evidence sources reported.</Empty>
            ) : (
              <div className="table-scroll">
                <table className="grid">
                  <thead>
                    <tr>
                      <th>source</th>
                      <th>mode</th>
                      <th>health</th>
                      <th>detail</th>
                      <th>connection error</th>
                      <th>what to check</th>
                    </tr>
                  </thead>
                  <tbody>
                    {st.data.sources.map((s, i) => (
                      <tr key={`${s.source}-${i}`} className={s.error ? 'row-bad' : ''}>
                        <td>
                          <strong>{s.source}</strong>
                        </td>
                        <td>
                          <Badge tone={MODE_TONE[s.mode as keyof typeof MODE_TONE] ?? 'neutral'}>{s.mode}</Badge>
                        </td>
                        <td>{s.healthy ? <Badge tone="good">healthy</Badge> : <Badge tone="bad">unhealthy</Badge>}</td>
                        <td className="pre-wrap">{s.detail || <span className="muted">—</span>}</td>
                        <td>{s.error ? <pre className="pre-wrap error-text">{s.error}</pre> : <span className="muted">—</span>}</td>
                        <td className="small">{diagnose(s.mode, s.error, s.detail) ?? <span className="muted">—</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  )
}
