import { useMemo, useState } from 'react'
import { CHECKOUT_BASE, controlHealth, isScenarioCapture, postOrder, serviceHealth } from '../lib/api'
import type { ControlHealth, OrderRequest } from '../lib/api'
import { CATALOG, MAX_QTY, lineAmount, money, type Product } from '../lib/catalog'
import { fmtMs, isOk, type HttpOutcome } from '../lib/http'
import { usePoll } from '../lib/usePoll'
import { RawBody, StatusBadge } from '../components/HttpResult'
import { Card, Empty, Notice, Pill, Spinner } from '../components/ui'

type Cart = Record<string, number>

interface Attempt {
  order: OrderRequest
  outcome?: HttpOutcome // undefined while in flight
}

function newOrderId(): string {
  const rnd = Math.floor(Math.random() * 36 ** 4).toString(36).padStart(4, '0')
  return `web-${Date.now().toString(36)}-${rnd}`
}

function QtyStepper(props: { value: number; onChange: (n: number) => void; label: string }) {
  const { value, onChange, label } = props
  return (
    <div className="stepper" role="group" aria-label={label}>
      <button onClick={() => onChange(Math.max(0, value - 1))} disabled={value <= 0} aria-label="decrease">
        -
      </button>
      <span className="mono" aria-live="polite">
        {value}
      </span>
      <button onClick={() => onChange(Math.min(MAX_QTY, value + 1))} disabled={value >= MAX_QTY} aria-label="increase">
        +
      </button>
    </div>
  )
}

function ProductCard({ p, qty, setQty }: { p: Product; qty: number; setQty: (n: number) => void }) {
  return (
    <article className="product">
      <div className="product-sku mono">{p.sku}</div>
      <h3>{p.name}</h3>
      <div className="product-price">{money(p.price)}</div>
      {qty === 0 ? (
        <button className="btn" onClick={() => setQty(1)}>
          Add to cart
        </button>
      ) : (
        <QtyStepper value={qty} onChange={setQty} label={`quantity of ${p.sku}`} />
      )}
    </article>
  )
}

function CheckoutHealth() {
  const { outcome } = usePoll(() => serviceHealth(CHECKOUT_BASE), 10000)
  if (!outcome) return <Pill tone="neutral">checkout-svc: checking</Pill>
  const body = outcome.bodyJson as { status?: string; version?: string } | undefined
  if (isOk(outcome) && body?.status === 'ok')
    return (
      <Pill tone="ok">
        checkout-svc /health ok · v{body.version} · {fmtMs(outcome.durationMs)}
      </Pill>
    )
  return <Pill tone="bad">checkout-svc /health: {outcome.kind === 'response' ? `HTTP ${outcome.status}` : 'unreachable'}</Pill>
}

function AttemptRow({ a }: { a: Attempt }) {
  const o = a.outcome
  const body = o?.bodyJson as { payment_id?: string } | undefined
  return (
    <li className="attempt">
      <div className="attempt-head">
        {o ? <StatusBadge o={o} /> : <Spinner label="waiting for checkout-svc" />}
        {o && <span className="mono">{fmtMs(o.durationMs)}</span>}
        <span className="mono strong">{a.order.order_id}</span>
      </div>
      <div className="attempt-meta">
        <span>
          {a.order.item} × {a.order.qty} = {money(a.order.amount)}
        </span>
        <span>customer {a.order.customer_id}</span>
        {body?.payment_id && <span className="mono">payment_id {body.payment_id}</span>}
        {o && <span className="muted">sent {new Date(o.startedAt).toLocaleTimeString()}</span>}
      </div>
      {o && <RawBody o={o} />}
      {o?.kind === 'proxy-error' && (
        <p className="hint">The 502 above came from the Vite dev proxy: checkout-svc did not accept the connection.</p>
      )}
    </li>
  )
}

export function ShopPage() {
  const [cart, setCart] = useState<Cart>({})
  const [customerId, setCustomerId] = useState(() => String(1 + Math.floor(Math.random() * 20000)))
  const [note, setNote] = useState('')
  const [attempts, setAttempts] = useState<Attempt[]>([])
  const [submitting, setSubmitting] = useState(false)
  const lockPoll = usePoll(controlHealth, 10000)

  const lock =
    lockPoll.outcome && isOk(lockPoll.outcome) ? (lockPoll.outcome.bodyJson as ControlHealth).lock : null
  const captureRunning = isScenarioCapture(lock)

  const lines = useMemo(
    () =>
      CATALOG.filter((p) => (cart[p.sku] ?? 0) > 0).map((p) => ({
        p,
        qty: cart[p.sku],
        amount: lineAmount(p.price, cart[p.sku]),
      })),
    [cart],
  )
  const total = lines.reduce((s, l) => s + l.amount, 0)
  const cid = Number(customerId)
  const cidValid = Number.isInteger(cid) && cid > 0

  const setQty = (sku: string, n: number) => setCart((c) => ({ ...c, [sku]: n }))

  async function checkout() {
    setSubmitting(true)
    // checkout-svc accepts one item per order, so each cart line is its own POST /orders.
    for (const l of lines) {
      const order: OrderRequest = {
        order_id: newOrderId(),
        customer_id: cid,
        item: l.p.sku,
        qty: l.qty,
        amount: l.amount,
        currency: 'USD',
        ...(note.trim() ? { note: note.trim() } : {}),
      }
      setAttempts((xs) => [{ order }, ...xs])
      const outcome = await postOrder(order)
      setAttempts((xs) => xs.map((x) => (x.order.order_id === order.order_id ? { order, outcome } : x)))
      if (isOk(outcome)) setCart((c) => ({ ...c, [l.p.sku]: 0 }))
    }
    setSubmitting(false)
  }

  return (
    <div className="shop-grid">
      <div className="shop-main">
        <Card
          title="Products"
          aside={<Pill tone="info">demo catalog (static list)</Pill>}
        >
          <p className="hint">
            The shop services expose no catalog, stock or pricing endpoint. These are the SKUs the demo system
            already sends to checkout-svc; names and prices are display values used only to compute the order
            amount. No stock levels are tracked by the backend.
          </p>
          <div className="products">
            {CATALOG.map((p) => (
              <ProductCard key={p.sku} p={p} qty={cart[p.sku] ?? 0} setQty={(n) => setQty(p.sku, n)} />
            ))}
          </div>
        </Card>

        <Card
          title="Order results"
          aside={
            attempts.length > 0 && (
              <button className="link" onClick={() => setAttempts([])} disabled={submitting}>
                clear
              </button>
            )
          }
        >
          {attempts.length === 0 ? (
            <Empty>No orders sent yet. Each result shows the HTTP status, duration and body exactly as returned.</Empty>
          ) : (
            <ul className="attempts">
              {attempts.map((a) => (
                <AttemptRow key={a.order.order_id} a={a} />
              ))}
            </ul>
          )}
        </Card>
      </div>

      <aside className="shop-side">
        <Card title="Cart" aside={<CheckoutHealth />}>
          {lines.length === 0 ? (
            <Empty>Your cart is empty.</Empty>
          ) : (
            <ul className="cart-lines">
              {lines.map((l) => (
                <li key={l.p.sku}>
                  <div>
                    <div>{l.p.name}</div>
                    <div className="mono muted small">{l.p.sku}</div>
                  </div>
                  <QtyStepper value={l.qty} onChange={(n) => setQty(l.p.sku, n)} label={`quantity of ${l.p.sku}`} />
                  <div className="mono right">{money(l.amount)}</div>
                </li>
              ))}
              <li className="cart-total">
                <span>Total</span>
                <span className="mono">{money(total)}</span>
              </li>
            </ul>
          )}

          <label className="field">
            <span>Customer id</span>
            <input
              inputMode="numeric"
              value={customerId}
              onChange={(e) => setCustomerId(e.target.value.replace(/[^0-9]/g, ''))}
              aria-invalid={!cidValid}
            />
          </label>
          <label className="field">
            <span>Delivery note (optional)</span>
            <input value={note} maxLength={400} onChange={(e) => setNote(e.target.value)} placeholder="e.g. call on arrival" />
          </label>

          {captureRunning && lock && (
            <Notice tone="warn" title="Demo environment busy">
              A scenario capture is running ({lock.owner}, since {lock.started}). Orders are disabled so they do not
              land in the capture.
            </Notice>
          )}

          <button
            className="btn btn-primary btn-block"
            disabled={lines.length === 0 || !cidValid || submitting || captureRunning}
            onClick={() => void checkout()}
          >
            {submitting ? <Spinner label="Placing order…" /> : `Place order${lines.length > 1 ? 's' : ''} · ${money(total)}`}
          </button>
          <p className="hint">
            Sends a real <code>POST /orders</code> to checkout-svc per cart line (the service takes one item per
            order). The page waits at most 20 s.
          </p>
        </Card>
      </aside>
    </div>
  )
}
