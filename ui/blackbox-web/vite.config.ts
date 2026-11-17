import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// BlackBox UI dev server: http://127.0.0.1:5173, /api proxied to the BlackBox API (Agent 1) on 8700.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8700', changeOrigin: false },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8700', changeOrigin: false },
    },
  },
})
