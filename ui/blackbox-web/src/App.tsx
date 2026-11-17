import { useEffect } from 'react'
import { startHealthPolling, useBackend } from './api'
import { href, useRoute } from './hooks'
import { Badge } from './components/ui'
import { ProjectView } from './views/ProjectView'
import { StartView } from './views/StartView'
import { JobView } from './views/JobView'
import { RunsView } from './views/RunsView'
import { RunDetailView } from './views/RunDetailView'
import { OverviewView } from './views/OverviewView'
import { CompareView } from './views/CompareView'
import { IncidentView } from './views/IncidentView'

const NAV = [
  { key: 'overview', label: 'Overview', to: href('overview') },
  { key: 'connections', label: 'Connections', to: href('connections') },
  { key: 'start', label: 'Start investigation', to: href('start') },
  { key: 'runs', label: 'Results & history', to: href('runs') },
]

function BackendIndicator() {
  const b = useBackend()
  if (b.mode === 'checking') return <Badge tone="muted">checking API…</Badge>
  if (b.mode === 'fixture')
    return (
      <Badge tone="bad" title={b.lastError ?? undefined}>
        API offline · DEV FIXTURE
      </Badge>
    )
  const gpu = b.health?.gpu
  return (
    <span className="indicator">
      <Badge tone="good">API connected</Badge>
      {gpu && (
        <Badge tone={gpu.busy ? 'warn' : 'muted'} title={gpu.busy ? `held by ${gpu.holder ?? '?'}` : undefined}>
          GPU {gpu.busy ? 'busy' : 'free'}
        </Badge>
      )}
      {b.health?.queue && (
        <Badge tone="muted">
          {b.health.queue.queued} queued · {b.health.queue.running} running
        </Badge>
      )}
    </span>
  )
}

export default function App() {
  useEffect(() => startHealthPolling(), [])
  const { path } = useRoute()
  const [section = 'overview', id, extra] = path

  let view
  if (section === 'start') view = <StartView />
  else if (section === 'connections' || section === 'project') view = <ProjectView />
  else if (section === 'incidents' && id) view = <IncidentView key={id} incidentId={id} />
  else if (section === 'compare' && id && extra) view = <CompareView key={`${id}/${extra}`} batch={id} incident={extra} />
  else if (section === 'jobs' && id) view = <JobView key={id} jobId={id} />
  else if (section === 'runs' && id) view = <RunDetailView key={id} runId={id} />
  else if (section === 'runs') view = <RunsView />
  else view = <OverviewView />

  const active =
    section === 'jobs' ? 'start' : section === 'project' ? 'connections' : section === 'compare' ? 'runs' : section === 'incidents' ? 'overview' : section

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href={href('overview')}>
          BlackBox
        </a>
        <nav>
          {NAV.map((n) => (
            <a key={n.key} href={n.to} className={active === n.key ? 'active' : ''}>
              {n.label}
            </a>
          ))}
        </nav>
        <BackendIndicator />
      </header>
      <main>{view}</main>
    </div>
  )
}
