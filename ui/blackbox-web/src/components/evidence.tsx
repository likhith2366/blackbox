import { createContext, useContext, useMemo, useState } from 'react'
import type { EvidenceRecord, EvidenceSource } from '../types'
import { api, errText } from '../api'
import { fmtTime, shortId } from '../format'
import { Badge, CopyButton, Empty } from './ui'

/** evidence_id -> record, built from records actually retrieved (evidence_seen / tool results). */
export const EvidenceIndex = createContext<Map<string, EvidenceRecord>>(new Map())
/** Saved run id whose records are shown (enables GET /api/runs/{run}/evidence/{id}); null while live. */
export const RunContext = createContext<string | null>(null)

export function buildIndex(records: EvidenceRecord[]): Map<string, EvidenceRecord> {
  const m = new Map<string, EvidenceRecord>()
  for (const r of records) if (!m.has(r.evidence_id)) m.set(r.evidence_id, r)
  return m
}

export function EvidenceCard({ rec }: { rec: EvidenceRecord }) {
  const attrs = Object.entries(rec.attributes ?? {})
  return (
    <div className="evidence-card">
      <div className="evidence-head">
        <code className="wrap">{rec.evidence_id}</code>
        <Badge tone="neutral">{rec.source}</Badge>
        {rec.service && <Badge tone="muted">{rec.service}</Badge>}
        {rec.truncated && (
          <Badge tone="warn" title="content was shortened before it reached the model">
            shortened
          </Badge>
        )}
        {rec.fixture && (
          <Badge tone="warn" title="team-authored demo data, not a live integration">
            fixture data
          </Badge>
        )}
        {typeof rec.attributes?.backend === 'string' && (
          <Badge tone={rec.attributes?.mode === 'live' ? 'info' : 'neutral'}
                 title={typeof rec.attributes?.query === 'string' ? `query: ${rec.attributes.query}` : undefined}>
            {String(rec.attributes.backend)} · {rec.attributes?.mode === 'live' ? 'live' : 'recorded'}
          </Badge>
        )}
        {rec.attributes?.live === true && rec.attributes?.backend === undefined && <Badge tone="info">live</Badge>}
        <CopyButton text={rec.evidence_id} label="copy id" />
        {rec.content_ref && /^https?:\/\//.test(rec.content_ref) && (
          <a className="small" href={rec.content_ref} target="_blank" rel="noreferrer">
            source
          </a>
        )}
      </div>
      {rec.title && <div className="evidence-title">{rec.title}</div>}
      <div className="evidence-meta muted small">
        event {fmtTime(rec.event_time)} · available {fmtTime(rec.available_time)}
        {rec.version ? ` · version ${rec.version}` : ''}
        {rec.content_ref ? ` · ref ${rec.content_ref}` : ''}
      </div>
      <RecordContent rec={rec} />
      <SourceLink rec={rec} />
      {attrs.length > 0 && (
        <details className="small">
          <summary>attributes ({attrs.length})</summary>
          <pre className="pre-wrap small">{JSON.stringify(rec.attributes, null, 1)}</pre>
        </details>
      )}
    </div>
  )
}

/** A clickable cited/returned evidence id. Expands the retrieved record inline. */
export function EvidenceChip({ id, extra }: { id: string; extra?: string }) {
  const index = useContext(EvidenceIndex)
  const rec = index.get(id)
  const [open, setOpen] = useState(false)
  return (
    <span className={`chip-wrap ${open ? 'chip-open' : ''}`}>
      <button
        type="button"
        className={`chip ${rec ? '' : 'chip-missing'}`}
        onClick={() => rec && setOpen((o) => !o)}
        title={rec ? `${id} — click to ${open ? 'hide' : 'show'} content` : `${id} — not among retrieved evidence`}
        aria-expanded={rec ? open : undefined}
      >
        <span className="chip-src">{rec ? (typeof rec.attributes?.backend === 'string' ? `${rec.attributes.backend}` : rec.source) : '?'}</span>
        <code>{shortId(id, 36)}</code>
        {rec?.truncated && <span className="chip-flag">shortened</span>}
        {extra && <span className="chip-flag">{extra}</span>}
        {!rec && <span className="chip-flag">not retrieved</span>}
      </button>
      {open && rec && <EvidenceCard rec={rec} />}
    </span>
  )
}

export function CitationList({ ids, empty = 'no citations' }: { ids: string[]; empty?: string }) {
  if (!ids.length) return <span className="muted small">{empty}</span>
  return (
    <div className="chips">
      {ids.map((id, i) => (
        <EvidenceChip key={`${id}-${i}`} id={id} />
      ))}
    </div>
  )
}

/** Long id lists (B0 evidence budget can omit thousands): show the first N, expandable. */
export function IdList({ ids, limit = 40 }: { ids: string[]; limit?: number }) {
  const [all, setAll] = useState(false)
  const shown = all ? ids : ids.slice(0, limit)
  if (!ids.length) return <span className="muted small">none</span>
  return (
    <div>
      <div className="id-list">
        {shown.map((id) => (
          <code key={id}>{id}</code>
        ))}
      </div>
      {ids.length > limit && (
        <button type="button" className="link" onClick={() => setAll((a) => !a)}>
          {all ? 'show fewer' : `show all ${ids.length.toLocaleString('en-US')}`}
        </button>
      )}
    </div>
  )
}


/** Records whose content contains a unified diff (commit records) are rendered with +/- colouring. */
function looksLikeDiff(text: string): boolean {
  return /^(diff --git|--- |\+\+\+ |@@ )/m.test(text)
}

export function RecordContent({ rec }: { rec: EvidenceRecord }) {
  const text = rec.content || '(no content)'
  if (!looksLikeDiff(text)) return <pre className="evidence-content">{text}</pre>
  return (
    <pre className="evidence-content diff">
      {text.split('\n').map((line, i) => {
        const cls =
          line.startsWith('+') && !line.startsWith('+++')
            ? 'diff-add'
            : line.startsWith('-') && !line.startsWith('---')
              ? 'diff-del'
              : line.startsWith('@@') || line.startsWith('diff --git')
                ? 'diff-hunk'
                : ''
        return (
          <span key={i} className={cls}>
            {line}
            {'\n'}
          </span>
        )
      })}
    </pre>
  )
}

/** Filterable list of retrieved evidence: source, service, time window and free-text search. */
export function EvidenceExplorer({ records, cited }: { records: EvidenceRecord[]; cited?: Set<string> }) {
  const [source, setSource] = useState('')
  const [service, setService] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [q, setQ] = useState('')
  const [onlyCited, setOnlyCited] = useState(false)
  const [openAll, setOpenAll] = useState(false)
  const sources = useMemo(() => [...new Set(records.map((r) => r.source))].sort(), [records])
  const services = useMemo(() => [...new Set(records.map((r) => r.service ?? ''))].sort(), [records])
  const lo = from ? Date.parse(from.endsWith('Z') ? from : from + 'Z') : NaN
  const hi = to ? Date.parse(to.endsWith('Z') ? to : to + 'Z') : NaN
  const needle = q.trim().toLowerCase()
  const shown = records.filter((r) => {
    if (source && r.source !== source) return false
    if (service && (r.service ?? '') !== service) return false
    if (onlyCited && !cited?.has(r.evidence_id)) return false
    const t = Date.parse(r.event_time ?? r.available_time ?? '')
    if (!Number.isNaN(lo) && (Number.isNaN(t) || t < lo)) return false
    if (!Number.isNaN(hi) && (Number.isNaN(t) || t > hi)) return false
    if (needle && !`${r.evidence_id} ${r.title} ${r.content}`.toLowerCase().includes(needle)) return false
    return true
  })
  return (
    <div>
      <div className="filters">
        <label>
          Source
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">all ({records.length})</option>
            {sources.map((s) => (
              <option key={s} value={s}>
                {s} ({records.filter((r) => r.source === s).length})
              </option>
            ))}
          </select>
        </label>
        <label>
          Service
          <select value={service} onChange={(e) => setService(e.target.value)}>
            <option value="">all</option>
            {services.map((s) => (
              <option key={s} value={s}>
                {s || '(none)'}
              </option>
            ))}
          </select>
        </label>
        <label>
          From (UTC)
          <input type="datetime-local" step="1" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label>
          To (UTC)
          <input type="datetime-local" step="1" value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
        <label className="grow">
          Search
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="id, title or content" />
        </label>
        {cited && (
          <label className="inline">
            <input type="checkbox" checked={onlyCited} onChange={(e) => setOnlyCited(e.target.checked)} /> cited only
          </label>
        )}
        <button type="button" onClick={() => setOpenAll((v) => !v)}>
          {openAll ? 'Collapse' : 'Expand'} all
        </button>
      </div>
      <p className="muted small">
        {shown.length} of {records.length} records. Time filters use the event time (or the available time when there
        is none).
      </p>
      {shown.length === 0 ? (
        <Empty>No retrieved record matches these filters.</Empty>
      ) : (
        <div className="explorer-list">
          {shown.map((r) => (
            <details key={r.evidence_id} className="explorer-item" open={openAll}>
              <summary>
                <Badge tone="neutral">{r.source}</Badge> <code className="wrap">{r.evidence_id}</code>{' '}
                {r.service && <span className="muted small">{r.service}</span>}{' '}
                <span className="muted small">{fmtTime(r.event_time ?? r.available_time)}</span>{' '}
                {cited?.has(r.evidence_id) && <Badge tone="info">cited</Badge>}
                {r.truncated && <Badge tone="warn">shortened</Badge>}
                <span className="explorer-title">{r.title}</span>
              </summary>
              <EvidenceCard rec={r} />
            </details>
          ))}
        </div>
      )}
    </div>
  )
}


/** Opens the record's original source through the API (GitHub link, snapshot file/line, local log file). */
function SourceLink({ rec }: { rec: EvidenceRecord }) {
  const runId = useContext(RunContext)
  const [src, setSrc] = useState<EvidenceSource | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  if (!runId) return null
  return (
    <div className="small">
      {!src && (
        <button
          type="button"
          className="link"
          disabled={busy}
          onClick={() => {
            setBusy(true)
            setErr(null)
            api.evidenceSource(runId, rec.evidence_id).then(
              (r) => setSrc(r.source),
              (e) => setErr(errText(e)),
            ).finally(() => setBusy(false))
          }}
        >
          {busy ? 'opening…' : 'open original source'}
        </button>
      )}
      {err && <span className="error-text"> {err}</span>}
      {src && (
        <div className="source-box">
          <Badge tone="neutral">{src.kind}</Badge>{' '}
          {src.url && (
            <a href={src.url} target="_blank" rel="noreferrer">
              {src.url}
            </a>
          )}
          {(src.file || src.path) && (
            <code className="wrap">
              {src.file ?? src.path}
              {src.line !== undefined && src.line !== null ? ` : ${src.line}` : ''}
            </code>
          )}
          {src.raw !== undefined && src.raw !== null && (
            <pre className="pre-wrap small">{typeof src.raw === 'string' ? src.raw : JSON.stringify(src.raw, null, 1)}</pre>
          )}
        </div>
      )}
    </div>
  )
}
