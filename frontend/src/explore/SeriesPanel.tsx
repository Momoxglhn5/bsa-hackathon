// Stored history from Data /series. Missing points break the line; nothing is interpolated.
import { useState } from 'react'
import { getSeries } from '../api/endpoints'
import type { DataEnvelope, TimeseriesDTO } from '../api/types'
import { Kicker, Seg, Spinner, Tag } from '../components/ui'
import { fmtDate, fmtFixed, fmtRatioPct, fmtUsd, num } from '../lib/format'
import { C } from '../lib/labels'
import type { PositionView } from '../lib/model'
import { useRadar } from '../state/radar'
import { useLoad } from '../state/useRequest'

type MetricId = 'ORACLE_PRICE' | 'MARKET_PRICE' | 'PT_IMPLIED_APY' | 'UTILIZATION' | 'BORROW_APY' | 'LIQUIDITY_USD'

const METRICS: { id: MetricId; label: string; entity: (p: PositionView) => string | null; fmt: (v: number) => string }[] = [
  { id: 'ORACLE_PRICE', label: 'Oracle price', entity: (p) => `morpho:${p.dto.chain_id}:${p.dto.market_id}`, fmt: (v) => fmtFixed(v, 4) },
  { id: 'MARKET_PRICE', label: 'PT price (USD)', entity: (p) => `asset:${p.strategy.pt.chain_id}:${p.strategy.pt.address}`, fmt: (v) => fmtFixed(v, 4) },
  { id: 'PT_IMPLIED_APY', label: 'Implied APY', entity: (p) => (p.strategy.pendle_market_address ? `pendle:${p.strategy.chain_id}:${p.strategy.pendle_market_address}` : null), fmt: (v) => fmtRatioPct(v, 2) },
  { id: 'UTILIZATION', label: 'Utilization', entity: (p) => `morpho:${p.dto.chain_id}:${p.dto.market_id}`, fmt: (v) => fmtRatioPct(v, 1) },
  { id: 'BORROW_APY', label: 'Borrow rate', entity: (p) => `morpho:${p.dto.chain_id}:${p.dto.market_id}`, fmt: (v) => fmtRatioPct(v, 2) },
  { id: 'LIQUIDITY_USD', label: 'Liquidity (USD)', entity: (p) => `morpho:${p.dto.chain_id}:${p.dto.market_id}`, fmt: (v) => fmtUsd(v) },
]

export function SeriesPanel({ sel }: { sel: PositionView }) {
  const r = useRadar()
  const [metric, setMetric] = useState<MetricId>('ORACLE_PRICE')
  const def = METRICS.find((m) => m.id === metric)!
  const entity = def.entity(sel)
  const key = entity ? `${r.source.id}|${entity}|${metric}` : null
  const req = useLoad<DataEnvelope<TimeseriesDTO>>(key, (signal) => getSeries(r.source, entity!, metric, { signal }))
  const series = req.data?.data ?? null

  return (
    <div className="panel stack gap-12 pad">
      <div className="row wrap between gap-10">
        <Kicker>HISTORY</Kicker>
        <Seg small label="Metric" options={METRICS.map((m) => ({ id: m.id, label: m.label }))} value={metric} onChange={setMetric} />
      </div>
      {req.loading && <Spinner label="Loading…" />}
      {!req.loading && req.error && <div className="note" style={{ fontSize: 11 }}>History unavailable for this metric.</div>}
      {!req.loading && series && <SeriesChart series={series} fmt={def.fmt} />}
    </div>
  )
}

function SeriesChart({ series, fmt }: { series: TimeseriesDTO; fmt: (v: number) => string }) {
  const pts = series.points.map((p) => ({ t: new Date(p.timestamp).getTime(), v: num(p.value) }))
  const known = pts.filter((p) => p.v !== null) as { t: number; v: number }[]
  if (known.length === 0) return <div className="note">History unavailable for this metric.</div>
  const W = 800, H = 140, P = 8
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1
  const vs = known.map((p) => p.v)
  const lo = Math.min(...vs), hi = Math.max(...vs)
  const span = hi - lo || Math.abs(hi) || 1
  const x = (t: number) => P + ((t - t0) / (t1 - t0 || 1)) * (W - 2 * P)
  const y = (v: number) => H - P - ((v - lo) / span) * (H - 2 * P)
  const segs: string[] = []
  let cur = ''
  for (const p of pts) {
    if (p.v === null) { if (cur) segs.push(cur); cur = ''; continue }
    cur += `${cur ? 'L' : 'M'}${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)} `
  }
  if (cur) segs.push(cur)
  const missing = pts.length - known.length
  const last = known[known.length - 1]
  return (
    <div className="stack gap-8">
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
        aria-label={`${series.metric}: ${known.length} points, current value ${fmt(last.v)}`}>
        <line x1={P} x2={W - P} y1={H - P} y2={H - P} stroke="rgba(163,180,202,0.15)" />
        {segs.map((d, i) => <path key={i} d={d} fill="none" stroke={C.gold} strokeWidth={1.6} vectorEffect="non-scaling-stroke" />)}
      </svg>
      <div className="row wrap between gap-10" style={{ fontSize: 10, color: C.unk }}>
        <span>{fmtDate(series.points[0].timestamp)} → {fmtDate(series.points[series.points.length - 1].timestamp)}</span>
        <span>min {fmt(lo)} · max {fmt(hi)} · current {fmt(last.v)}</span>
        {missing > 0 && <Tag color={C.cau}>{missing} MISSING POINT(S)</Tag>}
      </div>
    </div>
  )
}
