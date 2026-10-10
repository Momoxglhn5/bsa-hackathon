import { num, utc } from '../lib/format'
import type { Series } from '../lib/types'
import { StatusBadge } from './primitives'

const W = 360
const H = 64

/** Line over the stored points. MISSING points break the line instead of being filled. */
export default function Sparkline({ series, format }: { series: Series; format: (v: number) => string }) {
  const values = series.points.map((p) => num(p.value))
  const known = values.filter((v): v is number => v !== null)
  if (!known.length) return <p className="m-0 text-[11px] text-ink-4 italic">Every point is missing.</p>
  const min = Math.min(...known)
  const max = Math.max(...known)
  const span = max - min || 1
  const x = (i: number) => (values.length === 1 ? W / 2 : (i / (values.length - 1)) * W)
  const y = (v: number) => H - 6 - ((v - min) / span) * (H - 12)
  let d = ''
  values.forEach((v, i) => {
    if (v === null) return
    d += `${i > 0 && values[i - 1] !== null ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)} `
  })
  const missing = values.length - known.length
  const first = series.points[0]!
  const last = series.points.at(-1)!
  const lastValue = known.at(-1)!
  return (
    <figure className="m-0 flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-lg">{format(lastValue)}</span>
        <StatusBadge status={series.status} />
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-16 w-full"
        role="img"
        aria-label={`${series.metric} from ${format(known[0]!)} to ${format(lastValue)}, ${values.length} points`}
        preserveAspectRatio="none"
      >
        <path d={d} fill="none" stroke="#818cf8" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
      <figcaption className="flex justify-between text-[10px] text-ink-4">
        <span>{utc(first.timestamp).slice(0, 10)}</span>
        {missing > 0 && <span className="text-warn">{missing} missing, not filled</span>}
        <span>{utc(last.timestamp).slice(0, 10)}</span>
      </figcaption>
    </figure>
  )
}
