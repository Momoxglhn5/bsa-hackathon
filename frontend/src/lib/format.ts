/** Display helpers. Inputs are API decimal strings; parsing is for display only. */

export function num(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

export function pct(value: number | null, digits = 2): string {
  return value === null ? 'Unavailable' : `${(value * 100).toFixed(digits)} %`
}

export function amount(value: number | null, symbol?: string | null, digits = 0): string {
  if (value === null) return 'Unavailable'
  const text = value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
  return symbol ? `${text} ${symbol}` : text
}

export function signed(value: number, symbol: string, digits = 0): string {
  const sign = value < 0 ? '−' : value > 0 ? '+' : ''
  return `${sign}${amount(Math.abs(value), symbol, digits)}`
}

export function utc(iso: string | null): string {
  if (!iso) return 'Unavailable'
  return `${iso.slice(0, 16).replace('T', ' ')} UTC`
}

export function age(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`
  if (seconds < 172_800) return `${Math.round(seconds / 3600)} h`
  return `${Math.round(seconds / 86_400)} d`
}
