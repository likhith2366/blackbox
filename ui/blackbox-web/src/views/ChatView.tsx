import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { api, errText, useBackend } from '../api'
import { href, navigate, useLoad } from '../hooks'
import { fmtTime } from '../format'
import { Badge, Card, Empty, ErrorBox, Loading, type Tone } from '../components/ui'
import { CitationList, EvidenceIndex, buildIndex } from '../components/evidence'
import type { ChatSessionData, ChatTurn } from '../types'

const VERDICT_TONE: Record<string, Tone> = {
  verified: 'good',
  'partly verified': 'warn',
  'not verified': 'bad',
  'no evidence': 'muted',
}
const VERDICT_HELP: Record<string, string> = {
  verified: 'Every cited id was retrieved, and every concrete detail in the answer appears in the cited records.',
  'partly verified': 'Some cited ids or details could not be found in the retrieved evidence.',
  'not verified': 'The answer makes claims without retrieved evidence behind them.',
  'no evidence': 'The assistant said it could not find the answer in the evidence.',
}

function NewChat({ onStarted }: { onStarted: (s: ChatSessionData) => void }) {
  const incidents = useLoad(() => api.incidents(), [])
  const [mode, setMode] = useState<'live_stack' | 'snapshot'>('live_stack')
  const [alert, setAlert] = useState('checkout-svc:error_rate>0.1:15')
  const [incident, setIncident] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function start(ev: FormEvent) {
    ev.preventDefault()
    setBusy(true)
    setErr(null)
    try {
      const s = await api.chatStart(
        mode === 'live_stack' ? { mode, alert: alert.replace(/\s/g, '') } : { mode, incident_id: incident || incidents.data?.[0]?.incident_id },
      )
      onStarted(s)
    } catch (e) {
      setErr(errText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="form" onSubmit={start}>
      <fieldset>
        <legend>What should BlackBox look at?</legend>
        <label className="inline">
          <input type="radio" checked={mode === 'live_stack'} onChange={() => setMode('live_stack')} /> The running system (live
          logs, metrics, traces, deployments, GitHub)
        </label>
        <label className="inline">
          <input type="radio" checked={mode === 'snapshot'} onChange={() => setMode('snapshot')} /> A saved incident snapshot
        </label>
      </fieldset>
      {mode === 'live_stack' ? (
        <label>
          Alert that opens the incident
          <input value={alert} onChange={(e) => setAlert(e.target.value)} placeholder="checkout-svc:error_rate>0.1:15" />
          <span className="muted small">Evaluated in Prometheus. The incident starts two minutes before it fired.</span>
        </label>
      ) : (
        <label>
          Incident
          <select value={incident} onChange={(e) => setIncident(e.target.value)}>
            {(incidents.data ?? []).map((i) => (
              <option key={i.incident_id} value={i.incident_id}>
                {i.incident_id} — {i.alert.title}
              </option>
            ))}
          </select>
        </label>
      )}
      <div>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? 'Opening…' : 'Start conversation'}
        </button>
      </div>
      {err && <ErrorBox error={err} />}
    </form>
  )
}

function AssistantTurn({ t }: { t: ChatTurn }) {
  const v = t.verification
  return (
    <div className="msg msg-assistant">
      <div className="msg-meta">
        <span className="msg-who">BlackBox</span>
        {v && (
          <Badge tone={VERDICT_TONE[v.verdict] ?? 'neutral'} title={VERDICT_HELP[v.verdict]}>
            {v.verdict}
          </Badge>
        )}
        {t.confidence && <Badge tone="muted">{t.confidence} confidence</Badge>}
        <span className="muted small">{t.seconds}s</span>
      </div>
      <div className="msg-text">{t.text}</div>
      {t.citations.length > 0 && (
        <div className="msg-section">
          <div className="msg-label">Evidence cited</div>
          <CitationList ids={t.citations} />
        </div>
      )}
      {v && (v.unknown_ids.length > 0 || v.unsupported_tokens.length > 0) && (
        <div className="msg-check">
          {v.unknown_ids.length > 0 && (
            <div>
              Cited but never retrieved: <code>{v.unknown_ids.join(', ')}</code>
            </div>
          )}
          {v.unsupported_tokens.length > 0 && (
            <div>
              Not found in the cited evidence: <code>{v.unsupported_tokens.join(', ')}</code>
            </div>
          )}
        </div>
      )}
      {t.tools.length > 0 && (
        <details className="msg-tools">
          <summary>
            {t.tools.length} tool call{t.tools.length > 1 ? 's' : ''} ·{' '}
            {Array.from(new Set(t.tools.flatMap((x) => x.backends))).join(', ') || 'no records'}
          </summary>
          <ul className="plain">
            {t.tools.map((x, i) => (
              <li key={i} className="small">
                <code>{x.tool}</code> {x.auto && <Badge tone="muted">auto</Badge>} <span className="muted">{JSON.stringify(x.args)}</span>{' '}
                <Badge tone={x.status === 'ok' ? 'good' : x.status === 'empty' ? 'muted' : 'warn'}>{x.status}</Badge>{' '}
                {x.records} records {x.backends.length > 0 && <span className="muted">from {x.backends.join(', ')}</span>}
                {x.error && <div className="error-text">{x.error}</div>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

function Conversation({ initial }: { initial: ChatSessionData }) {
  const [s, setS] = useState(initial)
  const [text, setText] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const end = useRef<HTMLDivElement>(null)
  const index = useMemo(() => buildIndex(Object.values(s.evidence)), [s.evidence])
  // braces: newer browsers return a Promise from scrollIntoView, and React would call it as the effect's cleanup
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth' })
  }, [s.turns.length, pending])

  async function send(ev: FormEvent) {
    ev.preventDefault()
    const q = text.trim()
    if (!q || pending) return
    setPending(q)
    setText('')
    setErr(null)
    try {
      const r = await api.chatAsk(s.session_id, q)
      setS(r.session)
    } catch (e) {
      setErr(errText(e))
      setText(q)
    } finally {
      setPending(null)
    }
  }

  const inc = s.incident
  const suggestions = ['What changed right before the alert?', 'Which service is failing, and how?', 'Show me an example of a failing request.']
  return (
    <EvidenceIndex.Provider value={index}>
      <div className="chat-layout">
        <Card
          className="chat-card"
          title={
            <span>
              {inc.alert.title} <span className="muted small">{inc.incident_id}</span>
            </span>
          }
          actions={<Badge tone={s.mode === 'live' ? 'info' : 'neutral'}>{s.mode === 'live' ? 'live stack' : 'snapshot'}</Badge>}
        >
          <div className="muted small chat-context">
            {inc.alert.condition} · fired {fmtTime(inc.alert.fired_at)} · evidence up to {fmtTime(inc.investigation_cutoff)}
          </div>
          <div className="chat-thread">
            {s.turns.length === 0 && !pending && (
              <div className="chat-empty">
                <p>Ask about this incident. BlackBox looks up evidence with read-only tools and cites it.</p>
                <div className="chat-suggestions">
                  {suggestions.map((q) => (
                    <button key={q} type="button" className="small" onClick={() => setText(q)}>
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {s.turns.map((t, i) =>
              t.role === 'user' ? (
                <div key={i} className="msg msg-user">
                  <div className="msg-text">{t.text}</div>
                </div>
              ) : (
                <AssistantTurn key={i} t={t} />
              ),
            )}
            {pending && (
              <>
                <div className="msg msg-user">
                  <div className="msg-text">{pending}</div>
                </div>
                <div className="msg msg-assistant msg-pending">
                  <span className="dot-pulse" aria-hidden="true" /> Looking up evidence… this runs on the local model and can
                  take a minute or two.
                </div>
              </>
            )}
            <div ref={end} />
          </div>
          {err && <ErrorBox error={err} />}
          <form className="chat-input" onSubmit={send}>
            <textarea
              rows={2}
              value={text}
              placeholder="Ask a question about this incident…"
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) send(e as unknown as FormEvent)
              }}
              disabled={!!pending}
            />
            <button type="submit" className="primary" disabled={!text.trim() || !!pending}>
              Send
            </button>
          </form>
        </Card>
      </div>
    </EvidenceIndex.Provider>
  )
}

export function ChatView({ sessionId }: { sessionId?: string }) {
  const backend = useBackend()
  const sessions = useLoad(() => api.chatSessions(), [])
  const [data, setData] = useState<ChatSessionData | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    setData(null)
    setErr(null)
    if (sessionId) api.chatSession(sessionId).then(setData, (e) => setErr(errText(e)))
  }, [sessionId])

  if (backend.mode === 'fixture')
    return <ErrorBox error="The BlackBox API is not reachable, so the assistant cannot answer. Start the API and reload." />

  if (sessionId) {
    if (err) return <ErrorBox error={err} />
    if (!data) return <Loading what="conversation" />
    return <Conversation key={data.session_id} initial={data} />
  }
  return (
    <div className="page">
      <div className="two-col">
        <Card title="New conversation">
          <NewChat onStarted={(s) => navigate(href('chat', s.session_id))} />
        </Card>
        <Card title="Earlier conversations">
          {sessions.loading && <Loading what="conversations" />}
          {sessions.data && sessions.data.length === 0 && <Empty>No conversations yet.</Empty>}
          {sessions.data && sessions.data.length > 0 && (
            <div className="list">
              {sessions.data.map((c) => (
                <a className="list-row" key={c.session_id} href={href('chat', c.session_id)}>
                  <span className="list-main">
                    <span className="list-title">{c.title}</span>
                    <span className="list-meta">
                      {c.incident_id} · {c.turns / 2} question{c.turns === 2 ? '' : 's'} · {c.mode === 'live' ? 'live stack' : 'snapshot'}
                    </span>
                  </span>
                  <Badge tone={c.open ? 'good' : 'muted'}>{c.open ? 'open' : 'read only'}</Badge>
                </a>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
