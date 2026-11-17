import { useEffect, useState } from 'react'
import {
  CHECKOUT_BASE,
  PAYMENT_BASE,
  controlFaults,
  controlHealth,
  controlInject,
  controlReset,
  controlState,
  isScenarioCapture,
  serviceHealth,
} from '../lib/api'
import type { ControlHealth, ControlState, FaultInfo, LockHolder } from '../lib/api'
import { errorText, fmtMs, isOk, type HttpOutcome } from '../lib/http'
import { usePoll } from '../lib/usePoll'
import { HttpResult } from '../components/HttpResult'
import { Card, Empty, Notice, Pill, Spinner } from '../components/ui'
import { DemoIncidentCard, TrafficCard } from './DemoFlowCards'

const SERVICES: { name: string; base: string; port: number }[] = [
  { name: 'checkout-svc', base: CHECKOUT_BASE, port: 18101 },
  { name: 'payment-svc', base: PAYMENT_BASE, port: 18102 },
]

function ControlApiDown({ o }: { o: HttpOutcome }) {
  return (
    <Notice tone="bad" title="Demo control API not reachable">
      {errorText(o) ?? `HTTP ${o.status}`}. Start it from the repo root with <code>python -m demo.control_api</code>{' '}
      (http://127.0.0.1:8710).
    </Notice>
  )
}

function LockBanner({ lock }: { lock: LockHolder | null }) {
  if (!lock) return <Pill tone="ok">demo lock free</Pill>
  return (
    <Notice tone="warn" title={isScenarioCapture(lock) ? 'Scenario capture running' : 'Demo lock held'}>
      {lock.owner} (pid {lock.pid}) since {lock.started}. Inject and reset are refused until it is released.
    </Notice>
  )
}

function DirectHealth({ base }: { base: string }) {
  const { outcome } = usePoll(() => serviceHealth(base), 5000)
  if (!outcome) return <Spinner />
  const b = outcome.bodyJson as { status?: string; version?: string } | undefined
  if (isOk(outcome) && b?.status === 'ok')
    return (
      <Pill tone="ok">
        ok · v{b.version} · {fmtMs(outcome.durationMs)}
      </Pill>
    )
  return <Pill tone="bad">{outcome.kind === 'response' ? `HTTP ${outcome.status}` : 'no answer'}</Pill>
}

function HealthPanel({
  health,
  loading,
  refresh,
  nonce,
}: {
  health: HttpOutcome | null
  loading: boolean
  refresh: () => void
  nonce: number
}) {
  const body = health && isOk(health) ? (health.bodyJson as ControlHealth) : null
  return (
    <Card
      title="Service health"
      aside={
        <button className="btn btn-small" onClick={refresh} disabled={loading}>
          {loading ? <Spinner /> : 'Refresh'}
        </button>
      }
    >
      <table className="table">
        <thead>
          <tr>
            <th>Service</th>
            <th>Via control API</th>
            <th>Direct GET /health</th>
          </tr>
        </thead>
        <tbody>
          {SERVICES.map((s) => {
            const v = body?.services?.[s.name]
            return (
              <tr key={s.name}>
                <td>
                  <div>{s.name}</div>
                  <div className="mono muted small">127.0.0.1:{s.port}</div>
                </td>
                <td>
                  {!health ? (
                    <Spinner />
                  ) : !body ? (
                    <span className="muted">unavailable</span>
                  ) : v ? (
                    <Pill tone="ok">
                      {v.status} · v{v.version}
                    </Pill>
                  ) : (
                    <Pill tone="bad">no /health answer</Pill>
                  )}
                </td>
                <td>
                  <DirectHealth key={`${s.name}-${nonce}`} base={s.base} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="hint">Both columns are live: refreshed every 5 s.</p>
    </Card>
  )
}

interface ActionRecord {
  id: number
  label: string
  outcome?: HttpOutcome
}

function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(t)
  }, [])
  return <span className="mono">{((now - since) / 1000).toFixed(0)} s</span>
}

export function ControlsPage() {
  const health = usePoll(controlHealth, 5000)
  const state = usePoll(controlState, 5000)
  const faults = usePoll(controlFaults, 60000)
  const [selected, setSelected] = useState<string>('')
  const [busy, setBusy] = useState<{ label: string; since: number } | null>(null)
  const [actions, setActions] = useState<ActionRecord[]>([])
  // bumped after every control action so the direct /health column re-polls immediately
  const [healthNonce, setHealthNonce] = useState(0)

  const hb = health.outcome && isOk(health.outcome) ? (health.outcome.bodyJson as ControlHealth) : null
  const lock = hb?.lock ?? null
  const faultList = faults.outcome && isOk(faults.outcome) ? (faults.outcome.bodyJson as FaultInfo[]) : null
  const st = state.outcome && isOk(state.outcome) ? (state.outcome.bodyJson as ControlState) : null
  const apiDown = health.outcome && !isOk(health.outcome) ? health.outcome : null

  async function run(label: string, call: () => Promise<HttpOutcome>) {
    if (!window.confirm(`${label}?\n\nThis changes the running local demo (real commit/build/deploy or operator action).`))
      return
    const id = Date.now()
    setBusy({ label, since: id })
    setActions((xs) => [{ id, label }, ...xs])
    const outcome = await call()
    setActions((xs) => xs.map((x) => (x.id === id ? { ...x, outcome } : x)))
    setBusy(null)
    setHealthNonce((n) => n + 1)
    void health.refresh()
    void state.refresh()
  }

  // The lock is enforced by the control API (409); the buttons stay usable so its answer is shown verbatim.
  const disabled = !!busy || !!apiDown

  return (
    <div className="controls">
      <Notice tone="info" title="Local demo controls">
        Operator-only area for the local demo (demo/control_api.py on 127.0.0.1:8710). Fault ids are shown only
        here; nothing on this page is sent to BlackBox.
      </Notice>

      {apiDown && <ControlApiDown o={apiDown} />}
      {hb && <LockBanner lock={lock} />}

      <div className="controls-grid">
        <HealthPanel
          health={health.outcome}
          loading={health.loading}
          nonce={healthNonce}
          refresh={() => {
            setHealthNonce((n) => n + 1)
            void health.refresh()
          }}
        />

        <Card title="Applied since last reset">
          {!state.outcome ? (
            <Spinner label="loading" />
          ) : !st ? (
            <Empty>Unavailable ({errorText(state.outcome) ?? `HTTP ${state.outcome.status}`}).</Empty>
          ) : st.applied_since_reset.length === 0 ? (
            <Empty>Nothing applied since the last reset (controller record).</Empty>
          ) : (
            <ul className="applied">
              {st.applied_since_reset.map((a, i) => (
                <li key={i}>
                  <span className="mono strong">{a.fault_id}</span>
                  <span className="muted small">{a.at}</span>
                  {a.commit && <span className="mono small">commit {a.commit.slice(0, 7)}</span>}
                  {a.deployments && a.deployments.length > 0 && (
                    <span className="mono small">{a.deployments.join(', ')}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title="Inject / reset">
        {!faults.outcome ? (
          <Spinner label="loading supported faults" />
        ) : !faultList ? (
          <Empty>Supported faults unavailable ({errorText(faults.outcome) ?? `HTTP ${faults.outcome.status}`}).</Empty>
        ) : (
          <fieldset className="faults" disabled={disabled}>
            <legend className="sr-only">Supported faults</legend>
            {faultList.map((f) => (
              <label key={f.fault_id} className={`fault ${selected === f.fault_id ? 'fault-selected' : ''}`}>
                <input
                  type="radio"
                  name="fault"
                  value={f.fault_id}
                  checked={selected === f.fault_id}
                  onChange={() => setSelected(f.fault_id)}
                />
                <span>
                  <span className="mono strong">{f.fault_id}</span> <Pill tone="neutral">{f.kind}</Pill>
                  <span className="fault-desc">{f.description}</span>
                </span>
              </label>
            ))}
          </fieldset>
        )}
        <div className="actions">
          <button
            className="btn btn-danger"
            data-testid="inject"
            disabled={disabled || !selected}
            onClick={() => void run(`inject ${selected}`, () => controlInject(selected))}
          >
            Inject selected fault
          </button>
          <button className="btn" data-testid="reset" disabled={disabled} onClick={() => void run('reset to healthy baseline', controlReset)}>
            Reset to healthy baseline
          </button>
          {busy && (
            <span className="busy">
              <Spinner label={busy.label} /> <Elapsed since={busy.since} />
            </span>
          )}
        </div>
        <p className="hint">
          Inject and reset make real commits, builds and deploys, so they can take tens of seconds. While a scenario
          capture holds the demo lock the control API answers 409 and the message is shown below.
        </p>
      </Card>

      <div className="controls-grid">
        <TrafficCard lock={lock} disabled={!!apiDown} />
        <DemoIncidentCard disabled={!!busy || !!apiDown} />
      </div>

      <Card title="Control actions (this session)">
        {actions.length === 0 ? (
          <Empty>No control actions yet.</Empty>
        ) : (
          <ul className="attempts">
            {actions.map((a) => (
              <li key={a.id} className="attempt">
                <div className="attempt-head">
                  <span className="strong">{a.label}</span>
                  <span className="muted small">{new Date(a.id).toLocaleTimeString()}</span>
                </div>
                {a.outcome ? (
                  <>
                    {a.outcome.status === 409 && (
                      <Notice tone="warn" title="Refused by the control API">
                        {errorText(a.outcome)}
                      </Notice>
                    )}
                    <HttpResult o={a.outcome} />
                  </>
                ) : (
                  <Spinner label="waiting for control API" />
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}
