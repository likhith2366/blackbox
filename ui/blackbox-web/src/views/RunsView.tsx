import { useMemo, useState } from 'react'
import { api } from '../api'
import { href, navigate, useLoad } from '../hooks'
import { fmtSeconds, fmtTime } from '../format'
import { Badge, Card, Empty, ErrorBox, FixtureBanner, Loading, ReportStatusBadge } from '../components/ui'
import type { RunSummary } from '../types'

export function RunsView() {
  const st = useLoad(() => api.runs(), [])
  const [batch, setBatch] = useState('')
  const [system, setSystem] = useState('')
  const [q, setQ] = useState('')

  const rows = st.data ?? []
  const batches = useMemo(() => [...new Set(rows.map((r) => r.batch ?? ''))].sort(), [rows])
  const systems = useMemo(() => [...new Set(rows.map((r) => r.system ?? ''))].sort(), [rows])
  const filtered = rows.filter(
    (r) =>
      (!batch || r.batch === batch) &&
      (!system || r.system === system) &&
      (!q || `${r.run_id} ${r.incident_id ?? ''} ${r.headline ?? ''}`.toLowerCase().includes(q.toLowerCase())),
  )

  return (
    <div className="page">
      {st.fromFixture && <FixtureBanner />}
      <Card
        title="Runs"
        actions={
          <button type="button" onClick={st.reload}>
            Refresh
          </button>
        }
      >
        <div className="filters">
          <label>
            Batch
            <select value={batch} onChange={(e) => setBatch(e.target.value)}>
              <option value="">all</option>
              {batches.map((b) => (
                <option key={b} value={b}>
                  {b || '(none)'}
                </option>
              ))}
            </select>
          </label>
          <label>
            System
            <select value={system} onChange={(e) => setSystem(e.target.value)}>
              <option value="">all</option>
              {systems.map((s) => (
                <option key={s} value={s}>
                  {s || '(none)'}
                </option>
              ))}
            </select>
          </label>
          <label className="grow">
            Search
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="run id, incident, headline" />
          </label>
        </div>
        {st.loading && <Loading what="runs" />}
        {st.error && <ErrorBox error={st.error} onRetry={st.reload} />}
        {!st.loading && !st.error && filtered.length === 0 && (
          <Empty>{rows.length ? 'No runs match these filters.' : 'No saved runs found under runs/.'}</Empty>
        )}
        {filtered.length > 0 && <RunTable rows={filtered} />}
      </Card>
    </div>
  )
}

function RunTable({ rows }: { rows: RunSummary[] }) {
  return (
    <div className="table-scroll">
      <table className="grid runs-table">
        <thead>
          <tr>
            <th>run</th>
            <th>system</th>
            <th>incident</th>
            <th>ablation</th>
            <th>outcome</th>
            <th>headline</th>
            <th className="num">runtime</th>
            <th className="num">rounds</th>
            <th className="num">tool calls</th>
            <th>mode</th>
            <th>code rev.</th>
            <th>saved</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.run_id} className="clickable" onClick={() => navigate(`${href('runs', r.run_id)}?from=history`)}>
              <td>
                <a href={`${href('runs', r.run_id)}?from=history`} onClick={(e) => e.stopPropagation()}>
                  <code>{r.run_id}</code>
                </a>
                {r.batch && <div className="muted small">{r.batch}</div>}
              </td>
              <td>{r.system ?? '—'}</td>
              <td>{r.incident_id ?? '—'}</td>
              <td>{r.ablation ?? '—'}</td>
              <td>
                {r.status && r.status !== 'completed' && <Badge tone="bad">run {r.status}</Badge>}{' '}
                <ReportStatusBadge status={r.report_status} />
              </td>
              <td className="headline-cell">{r.headline ?? '—'}</td>
              <td className="num">{fmtSeconds(r.wall_seconds)}</td>
              <td className="num">{r.rounds ?? '—'}</td>
              <td className="num">{r.tool_calls ?? '—'}</td>
              <td>{r.mode ?? '—'}</td>
              <td>
                <code className="small">{r.code_revision ? r.code_revision.slice(0, 12) : '—'}</code>
              </td>
              <td className="small">{fmtTime(r.modified)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
