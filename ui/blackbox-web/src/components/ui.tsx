import { useState, type ReactNode } from 'react'
import { humanize, fmtNum } from '../format'

export type Tone = 'neutral' | 'good' | 'warn' | 'bad' | 'info' | 'muted'

export function Badge({ tone = 'neutral', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  )
}

const REPORT_TONE: Record<string, Tone> = {
  diagnosis: 'info',
  insufficient_evidence: 'warn',
  no_fault_observed: 'good',
  failed: 'bad',
}
const REPORT_LABEL: Record<string, string> = {
  diagnosis: 'Diagnosis',
  insufficient_evidence: 'Insufficient evidence',
  no_fault_observed: 'No fault observed',
  failed: 'Failed',
}
export function ReportStatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <Badge tone="bad">No report</Badge>
  return <Badge tone={REPORT_TONE[status] ?? 'neutral'}>{REPORT_LABEL[status] ?? humanize(status)}</Badge>
}

const JOB_TONE: Record<string, Tone> = {
  queued: 'muted',
  waiting_gpu: 'warn',
  running: 'info',
  cancel_requested: 'warn',
  completed: 'good',
  failed: 'bad',
  cancelled: 'muted',
}
export function JobStatusBadge({ status }: { status: string }) {
  return <Badge tone={JOB_TONE[status] ?? 'neutral'}>{humanize(status)}</Badge>
}

const TOOL_TONE: Record<string, Tone> = {
  ok: 'good',
  empty: 'muted',
  unavailable: 'warn',
  error: 'bad',
  timeout: 'bad',
  invalid_args: 'bad',
  denied: 'bad',
  budget_exceeded: 'warn',
}
export function ToolStatusBadge({ status }: { status: string }) {
  return <Badge tone={TOOL_TONE[status] ?? 'neutral'}>{status}</Badge>
}

export function ConfidenceBadge({ c }: { c: string }) {
  return <Badge tone={c === 'high' ? 'info' : c === 'medium' ? 'neutral' : 'muted'}>{c} confidence</Badge>
}

export function Loading({ what }: { what: string }) {
  return <div className="state state-loading">Loading {what}…</div>
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="state state-error" role="alert">
      <strong>Something went wrong</strong>
      <p className="error-message">{error}</p>
      {onRetry && (
        <button type="button" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="state state-empty">{children}</div>
}

export function Card({
  title,
  actions,
  children,
  className = '',
}: {
  title?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="stat" title={hint}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}

/** Generic key/value table for real dicts (context accounting, meta). Arrays shown as counts + values. */
export function KVTable({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data)
  if (entries.length === 0) return <Empty>Nothing recorded.</Empty>
  return (
    <table className="kv">
      <tbody>
        {entries.map(([k, v]) => (
          <tr key={k}>
            <th>{humanize(k)}</th>
            <td>{renderValue(v)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function renderValue(v: unknown): ReactNode {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (typeof v === 'number') return fmtNum(v)
  if (typeof v === 'string') return v
  if (Array.isArray(v)) {
    if (v.every((x) => typeof x !== 'object' || x === null))
      return (
        <span>
          <span className="muted">({v.length})</span> <code className="wrap">{v.join(', ')}</code>
        </span>
      )
    return <pre className="pre-wrap small">{JSON.stringify(v, null, 1)}</pre>
  }
  if (typeof v === 'object') return <KVTable data={v as Record<string, unknown>} />
  return String(v)
}

export function FixtureBanner() {
  return (
    <div className="fixture-banner" role="status">
      <strong>DEV FIXTURE</strong> — the BlackBox API (127.0.0.1:8700) is not answering. Shown data are verbatim
      copies of saved files from <code>runs/</code> and <code>scenarios/snapshots/</code>, not live API responses.
    </div>
  )
}


/** Copies text (an evidence id or reference) to the clipboard; reports failure instead of hiding it. */
export function CopyButton({ text, label = 'copy' }: { text: string; label?: string }) {
  const [state, setState] = useState<'idle' | 'ok' | 'fail'>('idle')
  return (
    <button
      type="button"
      className="link small"
      title={`copy ${text}`}
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        if (!navigator.clipboard) {
          setState('fail')
        } else {
          navigator.clipboard.writeText(text).then(
            () => setState('ok'),
            () => setState('fail'),
          )
        }
        setTimeout(() => setState('idle'), 1500)
      }}
    >
      {state === 'ok' ? 'copied' : state === 'fail' ? 'copy failed' : label}
    </button>
  )
}
