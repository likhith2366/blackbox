import { useEffect, type ReactNode } from 'react'
import { startHealthPolling, useBackend } from './api'
import { href, useRoute } from './hooks'
import { ProjectView } from './views/ProjectView'
import { StartView } from './views/StartView'
import { JobView } from './views/JobView'
import { RunsView } from './views/RunsView'
import { RunDetailView } from './views/RunDetailView'
import { OverviewView } from './views/OverviewView'
import { CompareView } from './views/CompareView'
import { IncidentView } from './views/IncidentView'

type NavKey = 'overview' | 'start' | 'runs' | 'connections'

const ICONS: Record<NavKey, ReactNode> = {
  overview: <path d="M3 3h6v6H3zM11 3h6v4h-6zM11 9h6v8h-6zM3 11h6v6H3z" />,
  start: <path d="M6 4l10 6-10 6z" />,
  runs: <path d="M3 4h14M3 8h14M3 12h14M3 16h9" />,
  connections: <path d="M7 7a3 3 0 1 1 0 .01M13 13a3 3 0 1 1 0 .01M9 9l2 2M4 16l2-2M14 6l2-2" />,
}

const NAV: { group: string; items: { key: NavKey; label: string }[] }[] = [
  { group: 'Investigate', items: [{ key: 'overview', label: 'Overview' }, { key: 'start', label: 'New investigation' }] },
  { group: 'Results', items: [{ key: 'runs', label: 'Results & history' }] },
  { group: 'Setup', items: [{ key: 'connections', label: 'Connections' }] },
]

function Icon({ name }: { name: NavKey }) {
  return (
    <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[name]}
    </svg>
  )
}

function StatusPill({ tone, children, title }: { tone: 'ok' | 'warn' | 'bad' | 'idle'; children: ReactNode; title?: string }) {
  return (
    <span className={`status-pill status-${tone}`} title={title}>
      <span className="status-dot" aria-hidden="true" />
      {children}
    </span>
  )
}

function BackendStatus() {
  const b = useBackend()
  if (b.mode === 'checking') return <StatusPill tone="idle">Checking API</StatusPill>
  if (b.mode === 'fixture')
    return (
      <StatusPill tone="bad" title={b.lastError ?? undefined}>
        API offline · fixture data
      </StatusPill>
    )
  const gpu = b.health?.gpu
  return (
    <div className="status-group">
      <StatusPill tone="ok">API connected</StatusPill>
      {gpu && (
        <StatusPill tone={gpu.busy ? 'warn' : 'idle'} title={gpu.busy ? `held by ${gpu.holder ?? '?'}` : undefined}>
          GPU {gpu.busy ? 'busy' : 'free'}
        </StatusPill>
      )}
      {b.health?.queue && (
        <StatusPill tone="idle">
          {b.health.queue.queued} queued · {b.health.queue.running} running
        </StatusPill>
      )}
    </div>
  )
}

const TITLES: Record<string, { title: string; subtitle: string }> = {
  overview: { title: 'Overview', subtitle: 'Recent investigations, model jobs and connected sources' },
  start: { title: 'New investigation', subtitle: 'Choose an incident and a system, then start a run' },
  jobs: { title: 'Investigation job', subtitle: 'Live progress of a queued or running investigation' },
  runs: { title: 'Results & history', subtitle: 'Saved investigation runs and their reports' },
  run: { title: 'Run detail', subtitle: 'Report, evidence and round-by-round trace' },
  connections: { title: 'Connections', subtitle: 'BlackBox API, evidence sources and model status' },
  incidents: { title: 'Incident', subtitle: 'Alert, timeline and evidence available before the cutoff' },
  compare: { title: 'Compare systems', subtitle: 'Side-by-side reports for one incident' },
}

export default function App() {
  useEffect(() => startHealthPolling(), [])
  const { path } = useRoute()
  const [section = 'overview', id, extra] = path

  let view
  let titleKey = section
  if (section === 'start') view = <StartView />
  else if (section === 'connections' || section === 'project') {
    view = <ProjectView />
    titleKey = 'connections'
  } else if (section === 'incidents' && id) view = <IncidentView key={id} incidentId={id} />
  else if (section === 'compare' && id && extra) view = <CompareView key={`${id}/${extra}`} batch={id} incident={extra} />
  else if (section === 'jobs' && id) view = <JobView key={id} jobId={id} />
  else if (section === 'runs' && id) {
    view = <RunDetailView key={id} runId={id} />
    titleKey = 'run'
  } else if (section === 'runs') view = <RunsView />
  else {
    view = <OverviewView />
    titleKey = 'overview'
  }

  const active: NavKey =
    section === 'jobs' || section === 'start'
      ? 'start'
      : section === 'project' || section === 'connections'
        ? 'connections'
        : section === 'compare' || section === 'runs'
          ? 'runs'
          : 'overview'
  const head = TITLES[titleKey] ?? TITLES.overview
  const detail = id ? (extra ? `${id} / ${extra}` : id) : null

  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href={href('overview')}>
          <span className="brand-mark" aria-hidden="true">B</span>
          <span>
            <span className="brand-name">BlackBox</span>
            <span className="brand-sub">Incident investigator</span>
          </span>
        </a>
        <nav className="nav" aria-label="Main">
          {NAV.map((g) => (
            <div className="nav-group" key={g.group}>
              <div className="nav-group-label">{g.group}</div>
              {g.items.map((n) => (
                <a key={n.key} href={href(n.key)} className={`nav-item${active === n.key ? ' active' : ''}`}
                   aria-current={active === n.key ? 'page' : undefined}>
                  <Icon name={n.key} />
                  {n.label}
                </a>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">Local · qwen3:4b via Ollama</div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="topbar-title">
            {detail && (
              <div className="crumbs">
                <a href={href(active)}>{NAV.flatMap((g) => g.items).find((n) => n.key === active)?.label}</a>
                <span className="crumb-sep">/</span>
                <span className="crumb-current">{detail}</span>
              </div>
            )}
            <h1>{head.title}</h1>
            <p className="page-subtitle">{head.subtitle}</p>
          </div>
          <BackendStatus />
        </header>
        <main className="content">{view}</main>
      </div>
    </div>
  )
}
