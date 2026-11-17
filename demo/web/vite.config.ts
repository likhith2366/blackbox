import react from '@vitejs/plugin-react'
import { defineConfig, type ProxyOptions } from 'vite'
import type { ServerResponse } from 'node:http'

// Upstreams (CONTRACT section 12 / demo/paths.py). The browser only ever talks to
// this dev server, so no CORS is needed on the Python services.
const CHECKOUT = 'http://127.0.0.1:18101'
const PAYMENT = 'http://127.0.0.1:18102'
const CONTROL = 'http://127.0.0.1:8710'

// When an upstream is unreachable, answer with a JSON body that is clearly marked as
// produced by the dev proxy (header X-Demo-Proxy-Error), so the UI never presents it
// as a response of the service itself.
function route(target: string, prefix: string, upstreamName: string): ProxyOptions {
  return {
    target,
    changeOrigin: true,
    rewrite: (p) => p.replace(new RegExp('^' + prefix), ''),
    configure: (proxy) => {
      proxy.on('error', (err, _req, res) => {
        const r = res as ServerResponse
        if (!r || typeof r.writeHead !== 'function' || r.headersSent || r.writableEnded) return
        const code = (err as NodeJS.ErrnoException).code ?? 'ERROR'
        r.writeHead(502, { 'Content-Type': 'application/json', 'X-Demo-Proxy-Error': '1' })
        r.end(JSON.stringify({ proxy_error: code, upstream: upstreamName, target, message: err.message }))
      })
    },
  }
}

const proxy: Record<string, ProxyOptions> = {
  '/svc/checkout': route(CHECKOUT, '/svc/checkout', 'checkout-svc'),
  '/svc/payment': route(PAYMENT, '/svc/payment', 'payment-svc'),
  '/control': route(CONTROL, '/control', 'demo control API'),
}

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5174, strictPort: true, proxy },
  preview: { host: '127.0.0.1', port: 5174, strictPort: true, proxy },
})
