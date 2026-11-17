import { useState } from 'react'
import {
  controlDemoIncidents,
  controlSnapshot,
  controlTraffic,
  controlTrafficStatus,
  isScenarioCapture,
} from '../lib/api'
import type { DemoIncident, LockHolder, TrafficStatus } from '../lib/api'
import { errorText, isOk, type HttpOutcome } from '../lib/http'
import { usePoll } from '../lib/usePoll'
import { HttpResult } from '../components/HttpResult'
import { Card, Empty, Notice, Pill, Spinner } from '../components/ui'

/** Bounded, seeded order traffic through demo/loadgen.py (real POST /orders). */
export function TrafficCard({ lock, disabled }: { lock: LockHolder | null; disabled: boolean }) {
  const status = usePoll(controlTrafficStatus, 2000)
  const [rps, setRps] = useState(5)
  const [duration, setDuration] = useState(60)
  const [seed, setSeed] = useState(7)
  const [last, setLast] = useState<HttpOutcome | null>(null)
  const [sending, setSending] = useState(false)
  const ts = status.outcome && isOk(status.outcome) ? (status.outcome.bodyJson as TrafficStatus) : null
  const capture = isScenarioCapture(lock)

  async function start() {
    setSending(true)
    setLast(await controlTraffic(rps, duration, seed))
    setSending(false)
    void status.refresh()
  }

  return (
    <Card title="Generate traffic" aside={ts?.running ? <Pill tone="warn">running</Pill> : <Pill tone="neutral">idle</Pill>}>
      {capture && (
        <Notice tone="warn" title="Environment busy">
          A scenario capture owns the services; traffic is refused until it finishes.
        </Notice>
      )}
      <div className="form-row" data-testid="traffic-form">
        <label>
          rps (1-12){' '}
          <input type="number" min={1} max={12} value={rps} onChange={(e) => setRps(Number(e.target.value))} />
        </label>
        <label>
          duration s (5-180){' '}
          <input type="number" min={5} max={180} value={duration} onChange={(e) => setDuration(Number(e.target.value))} />
        </label>
        <label>
          seed <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
        </label>
        <button
          data-testid="traffic-start"
          className="btn"
          disabled={disabled || sending || capture || !!ts?.running}
          onClick={() => void start()}
        >
          {sending ? <Spinner /> : 'Start traffic'}
        </button>
      </div>
      {ts?.last ? (
        <p className="mono small" data-testid="traffic-status">
          {ts.running ? 'running' : 'finished'} · started {ts.last.started} · {ts.last.rps} rps for {ts.last.duration_s} s ·
          sent {ts.stats?.sent ?? 0} · ok {ts.stats?.ok ?? 0} · failed {ts.stats?.failed ?? 0}
        </p>
      ) : (
        <Empty>No traffic generated in this control-API process yet.</Empty>
      )}
      {last && last.status !== 200 && <HttpResult o={last} />}
      <p className="hint">
        Each request is a real POST /orders from demo/loadgen.py (seeded order stream). Counts are client-side results.
      </p>
    </Card>
  )
}

/** Turn the current demo session (since the last reset) into a DEMO incident snapshot. */
export function DemoIncidentCard({ disabled }: { disabled: boolean }) {
  const list = usePoll(controlDemoIncidents, 10000)
  const [busySince, setBusySince] = useState<number | null>(null)
  const [result, setResult] = useState<HttpOutcome | null>(null)
  const items = list.outcome && isOk(list.outcome) ? (list.outcome.bodyJson as DemoIncident[]) : null
  const created =
    result && isOk(result) ? (result.bodyJson as { incident_id: string; alert: { title: string; fired_at: string } }) : null

  async function create() {
    setBusySince(Date.now())
    setResult(await controlSnapshot())
    setBusySince(null)
    void list.refresh()
  }

  return (
    <Card title="Create demo incident snapshot">
      <p className="hint">
        Copies this session's real logs, spans and metrics plus the build/deploy records and repository history into a
        new snapshot (id INC-9xxx, development only, never part of evaluation). It needs an alert condition in the
        captured metrics (checkout error_rate &gt; 0.10 or p95 &gt; 1500 ms for 15 s).
      </p>
      <div className="actions">
        <button data-testid="snapshot-create" className="btn" disabled={disabled || busySince !== null} onClick={() => void create()}>
          {busySince !== null ? <Spinner label="building snapshot" /> : 'Create snapshot from this session'}
        </button>
      </div>
      {created && (
        <Notice tone="ok" title={`Created ${created.incident_id}`}>
          <span data-testid="snapshot-id">{created.incident_id}</span> · alert “{created.alert.title}” fired{' '}
          {created.alert.fired_at}. BlackBox lists it under /api/incidents.
        </Notice>
      )}
      {result && !isOk(result) && (
        <Notice tone="warn" title="Snapshot not created">
          {errorText(result) ?? `HTTP ${result.status}`}
        </Notice>
      )}
      <h4>Demo incidents</h4>
      {!items ? (
        <Spinner />
      ) : items.length === 0 ? (
        <Empty>None yet.</Empty>
      ) : (
        <ul className="applied" data-testid="demo-incidents">
          {items.map((d) => (
            <li key={d.incident_id}>
              <span className="mono strong">{d.incident_id}</span>
              <span className="muted small">alert {d.alert_fired_at}</span>
              <Pill tone="neutral">{d.status}</Pill>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
