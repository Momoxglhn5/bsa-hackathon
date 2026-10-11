// Shared visual primitives following the handoff design language.
import type { CSSProperties, ReactNode } from 'react'
import { C } from '../lib/labels'

export function Kicker({ children, gold, className = '' }: { children: ReactNode; gold?: boolean; className?: string }) {
  return <span className={`kicker${gold ? ' kicker--gold' : ''} ${className}`}>{children}</span>
}

export function Panel({ children, className = '', label, style }: { children: ReactNode; className?: string; label?: string; style?: CSSProperties }) {
  return <section className={`panel ${className}`} aria-label={label} style={style}>{children}</section>
}

export function Seg<T extends string | number>({ options, value, onChange, small, label, disabled }: {
  options: { id: T; label: string; title?: string; disabled?: boolean }[]
  value: T
  onChange: (v: T) => void
  small?: boolean
  label?: string
  disabled?: boolean
}) {
  return (
    <div className="row gap-4 wrap" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={String(o.id)} type="button" className={`seg${small ? ' seg--sm' : ''}`} aria-pressed={value === o.id}
          title={o.title} disabled={disabled || o.disabled} onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** Serif value; null/unknown text is rendered in the muted italic "unknown" style. */
export function Value({ text, unknown, size = 26, color }: { text: string; unknown?: boolean; size?: number; color?: string }) {
  if (unknown) return <span className="val-unknown" style={{ fontSize: Math.min(size, 18) }}>{text}</span>
  return <span className="val-serif" style={{ fontSize: size, color: color ?? C.text }}>{text}</span>
}

export function Kpi({ k, v, sub, unknown, color }: { k: string; v: string; sub?: string; unknown?: boolean; color?: string }) {
  return (
    <div className="kpi">
      <Kicker>{k}</Kicker>
      <Value text={v} unknown={unknown} color={color} />
      {sub && <span className="kpi-sub">{sub}</span>}
    </div>
  )
}

export function Metric({ k, v, sub, unknown, color }: { k: string; v: string; sub?: string; unknown?: boolean; color?: string }) {
  return (
    <div className="metric">
      <span>{k}</span>
      <Value text={v} unknown={unknown} size={22} color={color} />
      {sub && <span className="kpi-sub">{sub}</span>}
    </div>
  )
}

export function ListRow({ k, v, vColor, mono, unknown }: { k: ReactNode; v: ReactNode; vColor?: string; mono?: boolean; unknown?: boolean }) {
  return (
    <div className="list-row">
      <span style={{ color: C.textSoft }}>{k}</span>
      <span className={`${mono ? 'mono' : ''} ${unknown ? 'unknown' : ''}`} style={{ color: unknown ? undefined : vColor, fontSize: mono ? 11 : undefined }}>{v}</span>
    </div>
  )
}

export function Tag({ children, color = C.unk }: { children: ReactNode; color?: string }) {
  return <span className="tag" style={{ color }}>{children}</span>
}

export function Chip({ children, color = C.cau }: { children: ReactNode; color?: string }) {
  return <span className="chip" style={{ color, borderColor: `${color}73` }}>{children}</span>
}

export function Spinner({ label }: { label: string }) {
  return (
    <span className="row gap-8" role="status" style={{ fontSize: 12, color: C.sec }}>
      <span className="spinner" aria-hidden="true" />
      {label}
    </span>
  )
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="alert alert--warn row gap-12 wrap between" role="alert">
      <span>{message}</span>
      {onRetry && <button type="button" className="btn" onClick={onRetry}>Retry</button>}
    </div>
  )
}

const STATUS_LABEL: Record<string, string> = {
  OK: 'OK', CALCULATED: 'CALCULATED', PARTIAL: 'PARTIAL', CONDITIONAL: 'CONDITIONAL', NOT_APPLICABLE: 'NOT APPLICABLE',
  MISSING: 'MISSING', UNAVAILABLE: 'UNAVAILABLE', INSUFFICIENT_DATA: 'INSUFFICIENT DATA', STALE: 'STALE', ERROR: 'ERROR',
  EXIT_NOT_FEASIBLE: 'EXIT NOT FEASIBLE', QUOTE_UNAVAILABLE: 'QUOTE UNAVAILABLE',
}

export function StatusBadge({ status }: { status: string }) {
  const color = ['OK', 'CALCULATED', 'CONDITIONAL', 'VERIFIED_ACTIVE'].includes(status) ? C.pos
    : ['NOT_APPLICABLE', 'MISSING', 'UNAVAILABLE'].includes(status) ? C.unk
      : ['EXIT_NOT_FEASIBLE', 'ERROR'].includes(status) ? C.dan : C.cau
  return <Tag color={color}>{STATUS_LABEL[status] ?? status}</Tag>
}

export function NodeGlyph({ color, size = 8, ring }: { color: string; size?: number; ring?: boolean }) {
  return <span aria-hidden="true" style={{ width: size, height: size, flex: 'none', display: 'inline-block', borderRadius: '50%', background: ring ? 'transparent' : color, border: ring ? `1.5px solid ${color}` : undefined }} />
}
