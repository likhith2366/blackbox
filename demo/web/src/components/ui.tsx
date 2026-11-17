import type { ReactNode } from 'react'

export function Card(props: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${props.className ?? ''}`}>
      {(props.title || props.aside) && (
        <header className="card-head">
          <h2>{props.title}</h2>
          {props.aside && <div className="card-aside">{props.aside}</div>}
        </header>
      )}
      {props.children}
    </section>
  )
}

export type Tone = 'ok' | 'bad' | 'warn' | 'neutral' | 'info'

export function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`pill pill-${tone}`}>{children}</span>
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner-wrap" role="status">
      <span className="spinner" aria-hidden="true" />
      {label && <span>{label}</span>}
    </span>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>
}

export function Notice({ tone, title, children }: { tone: Tone; title?: ReactNode; children?: ReactNode }) {
  return (
    <div className={`notice notice-${tone}`} role={tone === 'bad' ? 'alert' : undefined}>
      {title && <strong>{title}</strong>}
      {children && <div>{children}</div>}
    </div>
  )
}
