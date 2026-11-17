import { httpCall } from './http'

// Paths go through the Vite proxy (vite.config.ts).
export const CHECKOUT_BASE = '/svc/checkout'
export const PAYMENT_BASE = '/svc/payment'
export const CONTROL_BASE = '/control/api'

/** Client-side wait limit for one order; checkout-svc's own payment timeouts are far below this. */
export const ORDER_TIMEOUT_MS = 20000

/** Exact payload accepted by checkout-svc POST /orders (services/checkout/app.py). */
export interface OrderRequest {
  order_id: string
  customer_id: number
  item: string
  qty: number
  amount: number
  currency: 'USD'
  note?: string
}

export const postOrder = (o: OrderRequest) =>
  httpCall('POST', `${CHECKOUT_BASE}/orders`, o, ORDER_TIMEOUT_MS)

export const serviceHealth = (base: string) => httpCall('GET', `${base}/health`, undefined, 4000)

// ---- demo control API (demo/control_api.py) ----
export interface ServiceHealthBody {
  status: string
  service: string
  version: string
}
export interface LockHolder {
  pid: number
  owner: string
  started: string
}
export interface ControlHealth {
  services: Record<string, ServiceHealthBody | null>
  lock: LockHolder | null
}
export interface FaultInfo {
  fault_id: string
  description: string
  kind: string
}
export interface AppliedFault {
  fault_id: string
  at: string
  commit?: string | null
  deployments?: string[] | null
}
export interface ControlState {
  applied_since_reset: AppliedFault[]
  lock: LockHolder | null
}

export const controlHealth = () => httpCall('GET', `${CONTROL_BASE}/health`, undefined, 8000)
export const controlFaults = () => httpCall('GET', `${CONTROL_BASE}/faults`, undefined, 8000)
export const controlState = () => httpCall('GET', `${CONTROL_BASE}/state`, undefined, 8000)
// Reset / inject build and deploy real artifacts; they can take a while. No client timeout.
export const controlReset = () => httpCall('POST', `${CONTROL_BASE}/reset`, {})
export const controlInject = (fault_id: string) =>
  httpCall('POST', `${CONTROL_BASE}/inject`, { fault_id })

export function isScenarioCapture(lock: LockHolder | null | undefined): boolean {
  return !!lock && lock.owner.toLowerCase().startsWith('scenario capture')
}

// ---- traffic generation and demo incidents (demo/control_api.py) ----
export interface TrafficInfo {
  started: string
  rps: number
  duration_s: number
  seed: number
}
export interface TrafficStatus {
  running: boolean
  last: TrafficInfo | null
  stats?: { sent: number; ok: number; failed: number }
}
export interface DemoIncident {
  incident_id: string
  alert_fired_at: string | null
  status: string
}
export const controlTraffic = (rps: number, duration_s: number, seed: number) =>
  httpCall('POST', `${CONTROL_BASE}/traffic`, { rps, duration_s, seed })
export const controlTrafficStatus = () => httpCall('GET', `${CONTROL_BASE}/traffic`, undefined, 8000)
/** Builds a snapshot from real session telemetry; can take tens of seconds. */
export const controlSnapshot = () => httpCall('POST', `${CONTROL_BASE}/incidents/snapshot`, {})
export const controlDemoIncidents = () => httpCall('GET', `${CONTROL_BASE}/incidents/demo`, undefined, 8000)
