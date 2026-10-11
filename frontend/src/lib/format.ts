// Display-only conversions. Backend Decimal strings are never recomputed into results here.

export const UNAVAILABLE = 'Unavailable'

/** Parse a backend Decimal string for display; null stays null (unknown is never 0). */
export function num(v: string | number | null | undefined): number | null {
  if (v === null || v === undefined || v === '') return null
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? n : null
}

const nf = (min: number, max: number) => new Intl.NumberFormat('en-US', { minimumFractionDigits: min, maximumFractionDigits: max })
const int = nf(0, 0)

export function fmtInt(v: string | number | null | undefined, fallback = UNAVAILABLE): string {
  const n = num(v)
  return n === null ? fallback : int.format(Math.round(n))
}

export function fmtUsd(v: string | number | null | undefined, fallback = UNAVAILABLE): string {
  const n = num(v)
  return n === null ? fallback : `${int.format(Math.round(n))} USD`
}

/** Signed USD, e.g. "+2,599 USD" / "−8,000 USD". */
export function fmtUsdSigned(v: string | number | null | undefined, fallback = UNAVAILABLE): string {
  const n = num(v)
  if (n === null) return fallback
  const r = Math.round(n)
  return `${r > 0 ? '+' : r < 0 ? '−' : ''}${int.format(Math.abs(r))} USD`
}

/** Compact USD for sizes: 2 000 000 → "$2M". */
export function fmtUsdCompact(v: string | number | null | undefined, fallback = UNAVAILABLE): string {
  const n = num(v)
  if (n === null) return fallback
  if (Math.abs(n) >= 1e6) return `$${nf(0, 1).format(n / 1e6)}M`
  if (Math.abs(n) >= 1e3) return `$${nf(0, 0).format(n / 1e3)}k`
  return `$${int.format(n)}`
}

export function fmtAmount(v: string | number | null | undefined, unit = '', digits = 2, fallback = UNAVAILABLE): string {
  const n = num(v)
  return n === null ? fallback : `${nf(0, digits).format(n)}${unit ? ` ${unit}` : ''}`
}

/** Ratio (0.02) → "2.0%". */
export function fmtRatioPct(v: string | number | null | undefined, digits = 1, fallback = UNAVAILABLE): string {
  const n = num(v)
  return n === null ? fallback : `${nf(digits, digits).format(n * 100)}%`
}

/** Value already in percent ("93.3") → "93%". */
export function fmtPct(v: string | number | null | undefined, digits = 0, fallback = UNAVAILABLE): string {
  const n = num(v)
  return n === null ? fallback : `${nf(0, digits).format(n)}%`
}

export function fmtFixed(v: string | number | null | undefined, digits = 3, fallback = UNAVAILABLE): string {
  const n = num(v)
  return n === null ? fallback : nf(digits, digits).format(n)
}

const dateFmt = new Intl.DateTimeFormat('en-US', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
const dateTimeFmt = new Intl.DateTimeFormat('en-US', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC' })

export function fmtDate(iso: string | null | undefined, fallback = UNAVAILABLE): string {
  if (!iso) return fallback
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? fallback : dateFmt.format(d)
}

export function fmtDateTime(iso: string | null | undefined, fallback = UNAVAILABLE): string {
  if (!iso) return fallback
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? fallback : `${dateTimeFmt.format(d)} UTC`
}

export function shortAddr(addr: string | null | undefined): string {
  if (!addr) return '—'
  const a = addr.includes(':') ? addr.split(':').at(-1)! : addr
  return a.length > 14 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a
}

/**
 * User percent text → exact Decimal ratio string, without float rounding:
 * "2" → "0.02", "5,275" → "0.05275". Returns null when the text is not a number.
 */
export function percentTextToRatio(text: string): string | null {
  const t = text.trim().replace(',', '.').replace(/\s/g, '')
  if (!/^-?\d+(\.\d+)?$/.test(t)) return null
  const neg = t.startsWith('-')
  const [i, f = ''] = (neg ? t.slice(1) : t).split('.')
  const digits = i + f
  const point = i.length - 2 // moving the decimal point two places left
  let intPart: string
  let fracPart: string
  if (point <= 0) {
    intPart = '0'
    fracPart = '0'.repeat(-point) + digits
  } else {
    intPart = digits.slice(0, point)
    fracPart = digits.slice(point)
  }
  intPart = intPart.replace(/^0+(?=\d)/, '')
  fracPart = fracPart.replace(/0+$/, '')
  const out = fracPart ? `${intPart}.${fracPart}` : intPart
  return neg && out !== '0' ? `-${out}` : out
}

/** User decimal text → normalised Decimal string ("1 000,5" → "1000.5"), or null. */
export function decimalText(text: string): string | null {
  const t = text.trim().replace(/\s/g, '').replace(',', '.')
  return /^-?\d+(\.\d+)?$/.test(t) ? t : null
}

/** Days between two ISO instants (b − a), rounded down. */
export function daysBetween(a: string, b: string): number | null {
  const da = new Date(a).getTime()
  const db = new Date(b).getTime()
  if (Number.isNaN(da) || Number.isNaN(db)) return null
  return Math.floor((db - da) / 86_400_000)
}
