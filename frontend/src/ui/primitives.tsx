import type { ReactNode } from 'react'
import type { EdgeStatus, NodeKind } from '../lib/graph'
import type { DataStatus } from '../lib/types'

export const KIND: Record<NodeKind, { label: string; color: string }> = {
  market: { label: 'Lending market', color: '#a5b4fc' },
  pool: { label: 'Pendle market', color: '#7dd3fc' },
  asset: { label: 'Asset', color: '#cbd5e1' },
  oracle: { label: 'Oracle', color: '#c4b5fd' },
  irm: { label: 'Rate model', color: '#8391a7' },
  contract: { label: 'Contract', color: '#8391a7' },
  position: { label: 'Position', color: '#f3f4f6' },
}

export const VERIFICATION: Record<EdgeStatus, { label: string; color: string; border: string }> = {
  ONCHAIN_VERIFIED: { label: 'On-chain verified', color: '#34d399', border: '#065f46' },
  API_OBSERVED: { label: 'API observed', color: '#7dd3fc', border: '#1e4a63' },
  CURATED: { label: 'Curated', color: '#c7d2fe', border: '#3a4766' },
  UI_JOIN: { label: 'UI join', color: '#94a3b8', border: '#334155' },
  UNRESOLVED: { label: 'Unresolved', color: '#f9a8d4', border: '#5b2a45' },
}

const DATA_STATUS: Record<DataStatus, { color: string; border: string }> = {
  OK: { color: '#34d399', border: '#065f46' },
  PARTIAL: { color: '#fbbf24', border: '#4b3b12' },
  STALE: { color: '#fbbf24', border: '#4b3b12' },
  UNVERIFIED: { color: '#cbd5e1', border: '#475569' },
  MISSING: { color: '#94a3b8', border: '#334155' },
  MISMATCH: { color: '#f87171', border: '#7f1d1d' },
  ERROR: { color: '#f87171', border: '#7f1d1d' },
}

function Badge({ text, color, border, title }: { text: string; color: string; border: string; title?: string }) {
  return (
    <span
      title={title}
      className="inline-flex h-[22px] shrink-0 items-center rounded border px-2 font-mono text-[10.5px] whitespace-nowrap"
      style={{ color, borderColor: border }}
    >
      {text}
    </span>
  )
}

export function StatusBadge({ status }: { status: DataStatus }) {
  const s = DATA_STATUS[status]
  return <Badge text={status} color={s.color} border={s.border} title="Envelope status from the API" />
}

export function VerificationBadge({ status }: { status: EdgeStatus }) {
  const s = VERIFICATION[status]
  return <Badge text={status} color={s.color} border={s.border} title={s.label} />
}

export function EstimateBadge({ text }: { text: string }) {
  return <Badge text={text} color="#fbbf24" border="#4b3b12" />
}

export function SectionLabel({ children, id }: { children: ReactNode; id?: string }) {
  return (
    <h2 id={id} className="m-0 text-xs font-semibold tracking-[.08em] text-ink-2 uppercase">
      {children}
    </h2>
  )
}

export function Card({ children, className = '', label }: { children: ReactNode; className?: string; label?: string }) {
  return (
    <section aria-label={label} className={`rounded-lg border border-line bg-card p-4 ${className}`}>
      {children}
    </section>
  )
}

export function Notes({ items }: { items: string[] }) {
  if (!items.length) return null
  return (
    <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
      {items.map((text) => (
        <li
          key={text}
          className="flex gap-2 rounded-md border border-warn-line bg-[rgba(245,158,11,.07)] px-2.5 py-2 text-[11.5px] leading-snug text-[#fcd34d]"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true" className="mt-px shrink-0">
            <path d="M12 3l10 18H2z" />
            <path d="M12 10v5M12 18v.5" />
          </svg>
          <span>{text}</span>
        </li>
      ))}
    </ul>
  )
}

export function Segmented<T extends string | number>({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: { value: T; label: string }[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1.5">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
          className="h-8 cursor-pointer rounded-md border border-[#243049] bg-control px-3 text-xs text-ink-2 hover:bg-hover aria-pressed:border-accent aria-pressed:bg-accent-soft aria-pressed:text-ink"
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Facts({ rows }: { rows: { label: string; value: ReactNode; missing?: boolean }[] }) {
  return (
    <dl className="m-0 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-2 text-xs">
      {rows.map((r) => (
        <div key={r.label} className="contents">
          <dt className="text-ink-3">{r.label}</dt>
          <dd className={`m-0 text-right font-mono ${r.missing ? 'text-ink-4 italic' : ''}`}>{r.value}</dd>
        </div>
      ))}
    </dl>
  )
}
