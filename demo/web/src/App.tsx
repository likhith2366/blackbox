import { useEffect, useState } from 'react'
import { ShopPage } from './pages/ShopPage'
import { ControlsPage } from './pages/ControlsPage'

type Route = 'shop' | 'controls'

const routeFromHash = (): Route => (window.location.hash === '#/controls' ? 'controls' : 'shop')

export default function App() {
  const [route, setRoute] = useState<Route>(routeFromHash)
  useEffect(() => {
    const on = () => setRoute(routeFromHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  return (
    <div className={`app app-${route}`}>
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden="true">◼</span>
          <span>Demo Shop</span>
          <span className="brand-sub">checkout-svc → payment-svc → SQLite</span>
        </div>
        <nav className="tabs" aria-label="pages">
          <a href="#/" className={route === 'shop' ? 'active' : ''} aria-current={route === 'shop' ? 'page' : undefined}>
            Shop
          </a>
          <a
            href="#/controls"
            className={`tab-controls ${route === 'controls' ? 'active' : ''}`}
            aria-current={route === 'controls' ? 'page' : undefined}
          >
            Local demo controls
          </a>
        </nav>
      </header>
      <main className="content">{route === 'shop' ? <ShopPage /> : <ControlsPage />}</main>
      <footer className="foot">
        Local demo UI · talks only to checkout-svc (18101), payment-svc /health (18102) and the demo control API
        (8710) through the dev-server proxy · separate from BlackBox
      </footer>
    </div>
  )
}
