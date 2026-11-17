import { useEffect, useMemo, useState } from 'react'
import { api, errText } from '../api'
import { href, useLoad } from '../hooks'
import { fmtTime } from '../format'
import type { EvidenceRecord, RunDetail } from '../types'
import { Badge, Card, CopyButton, Empty, ErrorBox, FixtureBanner, Loading } from '../components/ui'
import { EvidenceCard, EvidenceExplorer } from '../components/evidence'

// Incident timeline from GET /api/incidents/{id}/timeline (snapshot read through the evidence backend,
// cutoff applied). Evidence retrieved by saved runs is a secondary view: when a timeline event's id was
// retrieved by a saved run, its full record can be expanded in place.

const MAX_RUNS = 6

interface TlEvent {
  t: string
  kind: 'alert' | 'cutoff' | 'window' | 'error' | 'deploy' | 'build' | 'commit'
  label: string
  rec?: EvidenceRecord
}

function kindOf(r: EvidenceRecord): TlEvent['kind'] | null {
  if (r.source === 'deployments') return 'deploy'
  if (r.source === 'builds') return 'build'
  if (r.source === 'commits') return 'commit'
  const level = String(r.attributes?.level ?? '').toUpperCase()
  if (r.source === 'logs' && (level === 'ERROR' || /^ERROR\b/.test(r.title))) return 'error'
  if (r.source === 'traces' && r.attributes?.status === 'error') return 'error'
  return null
}

export function IncidentView({ incidentId }: { incidentId: string }) {
  const incidents = useLoad(() => api.incidents(), [])
  const timeline = useLoad(() => api.timeline(incidentId), [incidentId])
  const [kinds, setKinds] = useState<Set<string>>(new Set())
  const [onlyRetrieved, setOnlyRetrieved] = useState(false)
  const runs = useLoad(() => api.runs(), [])
  const inc = incidents.data?.find((i) => i.incident_id === incidentId)
  const related = useMemo(() => (runs.data ?? []).filter((r) => r.incident_id === incidentId).slice(0, MAX_RUNS), [runs.data, incidentId])
  const [details, setDetails] = useState<RunDetail[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all(related.map((r) => api.run(r.run_id).then((d) => d.data))).then(
      (d) => !cancelled && setDetails(d),
      (e) => !cancelled && setErr(errText(e)),
    )
    return () => {
      cancelled = true
    }
  }, [related])

  const records = useMemo(() => {
    const m = new Map<string, EvidenceRecord>()
    for (const d of details) for (const e of d.output.evidence_seen ?? []) if (!m.has(e.evidence_id)) m.set(e.evidence_id, e)
    return [...m.values()]
  }, [details])

  const recIndex = useMemo(() => new Map(records.map((r) => [r.evidence_id, r])), [records])
  const allKinds = useMemo(() => [...new Set((timeline.data?.events ?? []).map((e) => e.kind))].sort(), [timeline.data])
  const snapEvents = (timeline.data?.events ?? []).filter(
    (e) => (kinds.size === 0 || kinds.has(e.kind)) && (!onlyRetrieved || (e.id && recIndex.has(e.id))),
  )

  const events = useMemo(() => {
    const ev: TlEvent[] = []
    if (inc) {
      ev.push({ t: inc.window_start, kind: 'window', label: 'incident window starts' })
      ev.push({ t: inc.alert.fired_at, kind: 'alert', label: `alert fired: ${inc.alert.title} (${inc.alert.condition})` })
      ev.push({ t: inc.investigation_cutoff, kind: 'cutoff', label: 'investigation cutoff (nothing later is visible)' })
    }
    for (const r of records) {
      const k = kindOf(r)
      const t = r.event_time ?? r.available_time
      if (k && t) ev.push({ t, kind: k, label: r.title || r.evidence_id, rec: r })
    }
    return ev.sort((a, b) => Date.parse(a.t) - Date.parse(b.t))
  }, [inc, records])

  return (
    <div className="page">
      {(incidents.fromFixture || runs.fromFixture) && <FixtureBanner />}
      <Card
        title={`Incident ${incidentId}`}
        actions={
          <a className="button" href={`${href('start')}?incident=${encodeURIComponent(incidentId)}`}>
            Investigate
          </a>
        }
      >
        {incidents.loading && <Loading what="incident" />}
        {incidents.error && <ErrorBox error={incidents.error} onRetry={incidents.reload} />}
        {incidents.data && !inc && <ErrorBox error={`No snapshot for ${incidentId}.`} />}
        {inc && (
          <div className="incident-box">
            <div>
              <strong>{inc.alert.title}</strong> <Badge tone="muted">{inc.alert.alert_id}</Badge>
            </div>
            <div className="small">
              {inc.alert.service} · <code>{inc.alert.condition}</code> · fired {fmtTime(inc.alert.fired_at)}
            </div>
            <div className="small">
              window {fmtTime(inc.window_start)} → {fmtTime(inc.window_end)} · cutoff {fmtTime(inc.investigation_cutoff)}
            </div>
            <div className="small">
              services {inc.services.join(', ')} · sources {inc.available_sources.join(', ')}
            </div>
          </div>
        )}
      </Card>

      <Card title="Timeline (snapshot: alert, deployments, builds, commits, error logs)">
        <p className="muted small">
          From <code>GET /api/incidents/{incidentId}/timeline</code>: read through the evidence backend with the investigation cutoff
          applied
          {timeline.data && (
            <>
              {' '}
              (window start {fmtTime(timeline.data.window_start)}, cutoff {fmtTime(timeline.data.investigation_cutoff)})
            </>
          )}
          .
        </p>
        {timeline.loading && <Loading what="timeline" />}
        {timeline.error && <ErrorBox error={timeline.error} onRetry={timeline.reload} />}
        {timeline.data && (
          <>
            <div className="filters">
              {allKinds.map((k) => (
                <label key={k} className="inline">
                  <input
                    type="checkbox"
                    checked={kinds.size === 0 || kinds.has(k)}
                    onChange={() =>
                      setKinds((prev) => {
                        const base = prev.size === 0 ? new Set(allKinds) : new Set(prev)
                        if (base.has(k)) base.delete(k)
                        else base.add(k)
                        return base.size === allKinds.length ? new Set() : base
                      })
                    }
                  />{' '}
                  {k} ({timeline.data!.events.filter((e) => e.kind === k).length})
                </label>
              ))}
              <label className="inline">
                <input type="checkbox" checked={onlyRetrieved} onChange={(e) => setOnlyRetrieved(e.target.checked)} /> only events a
                saved run retrieved
              </label>
            </div>
            {timeline.data.notes.length > 0 && (
              <ul className="small muted">
                {timeline.data.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            )}
            {snapEvents.length === 0 ? (
              <Empty>No events match these filters.</Empty>
            ) : (
              <ul className="tl">
                {snapEvents.map((e, i) => {
                  const rec = e.id ? recIndex.get(e.id) : undefined
                  const kindCls = e.kind === 'error_log' ? 'error' : e.kind
                  return (
                    <li key={`${e.id ?? 'x'}-${i}`} className={`tl-${kindCls}`}>
                      <span className="muted small">{fmtTime(e.time)}</span>{' '}
                      <Badge tone={e.kind === 'error_log' || e.kind === 'alert' ? 'bad' : 'neutral'}>{e.kind}</Badge>{' '}
                      {e.service && <span className="muted small">{e.service} </span>}
                      {e.text}{' '}
                      {e.id && (
                        <>
                          <code className="small">{e.id}</code> <CopyButton text={e.id} label="copy id" />
                          {rec ? (
                            <>
                              {' '}
                              <Badge tone="info">retrieved by a saved run</Badge>{' '}
                              <button type="button" className="link small" onClick={() => setOpen(open === e.id ? null : e.id!)}>
                                {open === e.id ? 'hide' : 'show'} record
                              </button>
                              {open === e.id && <EvidenceCard rec={rec} />}
                            </>
                          ) : null}
                        </>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </>
        )}
      </Card>

      <Card title="Events in evidence retrieved by saved runs">
        <p className="muted small">
          Secondary view: built from the evidence retrieved by {details.length} saved run(s) of this incident ({records.length} distinct
          records) plus the incident's alert, window and cutoff.
        </p>
        {err && <ErrorBox error={err} />}
        {runs.loading && <Loading what="saved runs" />}
        {!runs.loading && related.length === 0 && <Empty>No saved run of this incident yet, so only the alert and window are known.</Empty>}
        {events.length > 0 && (
          <ul className="tl">
            {events.map((e, i) => (
              <li key={i} className={`tl-${e.kind}`}>
                <span className="muted small">{fmtTime(e.t)}</span> <Badge tone={e.kind === 'error' || e.kind === 'alert' ? 'bad' : 'neutral'}>{e.kind}</Badge>{' '}
                {e.label}
                {e.rec && (
                  <>
                    {' '}
                    <button type="button" className="link small" onClick={() => setOpen(open === e.rec!.evidence_id ? null : e.rec!.evidence_id)}>
                      {open === e.rec.evidence_id ? 'hide' : 'show'} <code>{e.rec.evidence_id}</code>
                    </button>
                    <CopyButton text={e.rec.evidence_id} label="copy id" />
                    {open === e.rec.evidence_id && <EvidenceCard rec={e.rec} />}
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Saved runs of this incident">
        {related.length === 0 ? (
          <Empty>None.</Empty>
        ) : (
          <ul className="plain">
            {related.map((r) => (
              <li key={r.run_id}>
                <a href={href('runs', r.run_id)}>{r.run_id}</a> <span className="muted small">{r.system}</span>{' '}
                {r.batch && (
                  <a className="small" href={href('compare', r.batch, incidentId)}>
                    compare in {r.batch}
                  </a>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title={`Evidence search (${records.length} records retrieved across these runs)`}>
        <EvidenceExplorer records={records} />
      </Card>
    </div>
  )
}
