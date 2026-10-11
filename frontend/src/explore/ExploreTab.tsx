import { useMemo, useState } from 'react'
import type { GraphDTO, RiskResult, Valuation } from '../api/types'
import { Kicker, Kpi, ListRow, NodeGlyph, Panel, Tag, Value } from '../components/ui'
import { daysBetween, fmtAmount, fmtDate, fmtFixed, fmtPct, fmtRatioPct, fmtUsd, num, UNAVAILABLE } from '../lib/format'
import { C, DIMENSION_LABEL, FACTOR_LABEL, NODE_TYPE_LABEL } from '../lib/labels'
import { ltvBufferPts, nodeNames, reach, type PositionView } from '../lib/model'
import { useRadar } from '../state/radar'
import { SeriesPanel } from './SeriesPanel'

export function ExploreTab() {
  const r = useRadar()
  const sel = r.sel!
  const risk = r.loaded.risks[sel.strategyId]?.data ?? null
  const graph = r.graphFor(sel.strategyId)
  const valuation = risk?.position_risk[sel.id]
  const [reloadMsg, setReloadMsg] = useState('')
  const holders = r.positions.filter((p) => p.strategyId === sel.strategyId)

  const reload = async () => {
    await r.reload()
    setReloadMsg('Data up to date.')
    window.setTimeout(() => setReloadMsg(''), 2500)
  }

  return (
    <>
      <div className="row wrap between gap-12" style={{ padding: '0 4px', alignItems: 'flex-end' }}>
        <div className="stack" style={{ gap: 2 }}>
          <span className="serif" style={{ fontSize: 30, lineHeight: 1.05 }}>{sel.title}</span>
          <span style={{ fontSize: 12, color: C.sec }}>{sel.pair}{sel.maturity ? ` · matures ${fmtDate(sel.maturity)}` : ''}</span>
        </div>
        <div className="row wrap gap-8">
          {holders.length > 1 && (
            <div className="row gap-4" role="group" aria-label="Positions on this market">
              {holders.map((p) => (
                <button key={p.id} type="button" className="seg" aria-pressed={p.id === sel.id} onClick={() => r.select(p.id)}>{p.title}</button>
              ))}
            </div>
          )}
          <button type="button" className="btn btn--ghost" onClick={() => r.setDrawer({ kind: 'data' })}>Data</button>
          <button type="button" className="btn btn--ghost" onClick={() => r.setDrawer({ kind: 'method' })}>Methodology</button>
        </div>
      </div>

      {valuation?.liquidatable === true && (
        <div className="alert alert--danger" role="status">Position liquidatable at the oracle price (health factor {fmtFixed(valuation.health_factor)}).</div>
      )}

      <FinancialSummary sel={sel} valuation={valuation} />
      <Indicators risk={risk} />

      <section aria-label="Analysis" className="stack gap-14">
        <div className="panel row wrap between gap-12" style={{ padding: '12px 16px' }}>
          <span className="h-panel">Position analysis</span>
          <div className="row wrap gap-8">
            {reloadMsg && <span role="status" style={{ fontSize: 11, color: C.pos }}>{reloadMsg}</span>}
            <button type="button" className="btn" onClick={() => r.setDrawer({ kind: 'incidents' })}>Incident history</button>
            <button type="button" className="btn" onClick={reload} disabled={r.status === 'loading'}>{r.status === 'loading' ? 'Reloading…' : 'Reload data'}</button>
            <button type="button" className="btn btn--primary" onClick={() => r.setTab('simulate')}>Simulate a shock →</button>
          </div>
        </div>
        <div className="grid-auto" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
          <Watchlist sel={sel} risk={risk} graph={graph} valuation={valuation} />
          <MissingInfo risk={risk} />
          <CriticalDependencies sel={sel} graph={graph} />
        </div>
        <div className="grid-auto" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 380px), 1fr))', alignItems: 'start' }}>
          <RiskDimensions risk={risk} />
          <MarketPanel sel={sel} />
        </div>
        <SeriesPanel sel={sel} />
      </section>
    </>
  )
}

function FinancialSummary({ sel, valuation }: { sel: PositionView; valuation: Valuation | undefined }) {
  const hf = num(valuation?.health_factor)
  const hfColor = valuation?.liquidatable ? C.dan : hf !== null && hf < 1.1 ? C.cau : undefined
  return (
    <Panel className="kpis" label="Financial summary">
      <Kpi k="COLLATERAL" v={fmtUsd(valuation?.collateral_value_usd)} unknown={!valuation?.collateral_value_usd}
        sub={fmtAmount(sel.dto.collateral_amount, sel.ptName, 0)} />
      <Kpi k="DEBT" v={fmtAmount(sel.dto.borrow_amount, sel.loanName, 0)} unknown={sel.dto.borrow_amount === null}
        sub={valuation?.debt_value_usd ? `≈ ${fmtUsd(valuation.debt_value_usd)}` : undefined} />
      <Kpi k="EQUITY" v={fmtUsd(valuation?.equity_usd)} unknown={!valuation?.equity_usd} />
      <Kpi k="LTV (ORACLE)" v={fmtRatioPct(valuation?.ltv_ratio)} unknown={!valuation?.ltv_ratio} />
      <Kpi k="LLTV THRESHOLD" v={fmtRatioPct(valuation?.lltv_ratio)} unknown={!valuation?.lltv_ratio} />
      <Kpi k="HEALTH FACTOR" v={hf !== null ? fmtFixed(hf, 3) : UNAVAILABLE} unknown={hf === null} color={hfColor} />
    </Panel>
  )
}

function Indicators({ risk }: { risk: RiskResult | null }) {
  const index = num(risk?.risk_index)
  const conf = num(risk?.evidence_confidence_pct)
  const cov = num(risk?.completeness_pct)
  const items = [
    { k: 'RISK INDEX', v: index !== null ? `${fmtFixed(index, 0)} / 100` : 'Insufficient data', unknown: index === null, bar: index, color: index !== null && index >= 60 ? C.cau : undefined, title: 'Ordinal index from 0 to 100, not a failure probability.' },
    { k: 'DATA COVERAGE', v: fmtPct(cov), unknown: cov === null, bar: cov, color: cov !== null && cov < 50 ? C.cau : undefined, title: 'Share of the ten risk factors documented.' },
    { k: 'EVIDENCE CONFIDENCE', v: conf === null ? UNAVAILABLE : `${conf >= 75 ? 'High' : conf >= 50 ? 'Medium' : 'Low'} · ${fmtPct(conf)}`, unknown: conf === null, bar: conf, color: undefined, title: 'Quality and freshness of sources.' },
  ]
  return (
    <div className="grid-auto" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' }}>
      {items.map((i) => (
        <div key={i.k} className="panel stack gap-10" style={{ padding: '16px 20px' }} title={i.title}>
          <Kicker>{i.k}</Kicker>
          <Value text={i.v} unknown={i.unknown} size={30} color={i.color} />
          <div className="bar-track" aria-hidden="true">
            <div className="bar-fill" style={{ width: `${Math.max(0, Math.min(100, i.bar ?? 0))}%`, background: i.color ?? C.gold }} />
          </div>
        </div>
      ))}
    </div>
  )
}

const dotStyle = (c: string) => ({ width: 6, height: 6, borderRadius: '50%', background: c, flex: 'none' as const, marginTop: 6 })

function Watchlist({ sel, risk, graph, valuation }: { sel: PositionView; risk: RiskResult | null; graph: GraphDTO | null; valuation: Valuation | undefined }) {
  const r = useRadar()
  const names = useMemo(() => (graph ? nodeNames(graph, r.positions, r.loaded.catalog?.data ?? null) : null), [graph, r.positions, r.loaded.catalog])
  const items: { t: string; c: string }[] = []
  const buf = ltvBufferPts(valuation)
  if (buf !== null) items.push({ t: `Buffer before liquidation: ${fmtFixed(buf, 1)} LTV pts`, c: buf < 8 ? C.dan : buf < 15 ? C.cau : C.pos })
  if (sel.maturity && r.evaluatedAt) {
    const d = daysBetween(r.evaluatedAt, sel.maturity)
    if (d !== null) items.push({ t: d <= 0 ? 'PT has matured' : `PT matures in ${d} days`, c: d <= 30 ? C.cau : C.sec })
  }
  for (const e of graph?.edges.filter((x) => x.verification_status === 'UNRESOLVED') ?? []) {
    items.push({ t: `Unverified: ${names?.get(e.source)?.short ?? e.source} → ${names?.get(e.target)?.short ?? e.target}`, c: C.cau })
  }
  const top = [...(risk?.factor_explanations ?? [])].filter((f) => f.risk !== null).sort((a, b) => num(b.risk)! - num(a.risk)!).slice(0, 3)
  for (const f of top) items.push({ t: `${FACTOR_LABEL[f.factor_id] ?? f.factor_id}: ${fmtRatioPct(f.risk, 0)}`, c: num(f.risk)! >= 0.66 ? C.cau : C.gold })
  return (
    <div className="panel stack gap-10 pad">
      <Kicker>WATCHLIST</Kicker>
      {items.map((it, i) => (
        <div key={i} className="row gap-10" style={{ alignItems: 'flex-start', fontSize: 12, lineHeight: 1.5, color: C.textSoft, padding: '8px 0', borderTop: '1px solid rgba(163,180,202,0.1)' }}>
          <span style={dotStyle(it.c)} />
          <span>{it.t}</span>
        </div>
      ))}
    </div>
  )
}

// Only position-specific gaps: the graph's unknown dependency classes are the same constant for every strategy.
function MissingInfo({ risk }: { risk: RiskResult | null }) {
  const rows = (risk?.unknown_factors ?? []).map((f) => ({ k: FACTOR_LABEL[f] ?? f, v: 'Unknown' }))
  if (!rows.length) return null
  return (
    <div className="panel stack gap-10 pad">
      <Kicker>MISSING INFORMATION</Kicker>
      {rows.map((m) => (
        <div key={m.k} className="row between gap-10" style={{ fontSize: 12, padding: '8px 0', borderTop: '1px solid rgba(163,180,202,0.1)' }}>
          <span style={{ color: C.textSoft }}>{m.k}</span>
          <span style={{ color: C.unk, fontStyle: 'italic', textAlign: 'right' }}>{m.v}</span>
        </div>
      ))}
    </div>
  )
}

function CriticalDependencies({ sel, graph }: { sel: PositionView; graph: GraphDTO | null }) {
  const r = useRadar()
  const cat = r.loaded.catalog?.data ?? null
  const data = useMemo(() => {
    if (!graph) return null
    const R = reach(graph, sel.id)
    const names = nodeNames(graph, r.positions, cat)
    const positionsInGraph = graph.nodes.filter((n) => n.type === 'POSITION').map((n) => reach(graph, n.id))
    const unresolved = new Set(graph.edges.filter((e) => e.verification_status === 'UNRESOLVED').flatMap((e) => [e.source, e.target]))
    return [...R.keys()].filter((k) => k !== sel.id).filter((k) => {
      const n = graph.nodes.find((x) => x.id === k)
      return n && (['ORACLE', 'MARKET', 'SY', 'ASSET', 'BRIDGE', 'GOVERNANCE'].includes(n.type) || unresolved.has(k))
    }).sort((a, b) => R.get(a)! - R.get(b)!).slice(0, 7).map((k) => {
      const n = graph.nodes.find((x) => x.id === k)!
      const shared = positionsInGraph.filter((m) => m.has(k)).length
      return {
        id: k, name: names.get(k)?.name ?? k,
        tag: unresolved.has(k) ? 'UNVERIFIED' : shared > 1 ? `SHARED ×${shared}` : NODE_TYPE_LABEL[n.type].toUpperCase(),
        tagColor: unresolved.has(k) ? C.cau : C.unk,
      }
    })
  }, [graph, sel.id, r.positions, cat])
  return (
    <div className="panel stack gap-6 pad">
      <Kicker>KEY DEPENDENCIES</Kicker>
      {!graph && <span className="note">Graph unavailable.</span>}
      {data?.map((c) => (
        <button key={c.id} type="button" className="rel-btn" style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}
          onClick={() => { r.setPgAll(false); r.setPgNode(c.id); r.showPath(null); r.setPage('graph') }}>
          <NodeGlyph color={c.tagColor === C.cau ? C.cau : C.gold} />
          <span className="grow" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: C.text }}>{c.name}</span>
          <Tag color={c.tagColor}>{c.tag}</Tag>
        </button>
      ))}
    </div>
  )
}

function RiskDimensions({ risk }: { risk: RiskResult | null }) {
  const r = useRadar()
  if (!risk) return <div className="panel pad note">Risk analysis unavailable.</div>
  return (
    <div className="panel stack gap-12 pad">
      <Kicker>RISK DIMENSIONS</Kicker>
      {Object.entries(risk.dimension_scores).map(([dim, s]) => {
        const score = num(s.score)
        return (
          <div key={dim} className="stack gap-6" style={{ fontSize: 12 }}>
            <div className="row between gap-10">
              <span style={{ color: C.textSoft }}>{DIMENSION_LABEL[dim] ?? dim}</span>
              <span className="mono" style={{ fontSize: 11, color: score === null ? C.unk : C.text }}>{score === null ? 'Not covered' : `${fmtFixed(score, 0)} / 100`}</span>
            </div>
            <div className="bar-track" aria-hidden="true">
              <div className="bar-fill" style={{ width: `${score ?? 0}%`, background: score !== null && score >= 60 ? C.cau : C.gold }} />
            </div>
          </div>
        )
      })}
      <details>
        <summary style={{ cursor: 'pointer', fontSize: 11, color: C.sec }}>All {risk.factor_explanations.length} factors</summary>
        <div className="stack" style={{ marginTop: 8 }}>
          {risk.factor_explanations.map((f) => (
            <button key={f.factor_id} type="button" className="rel-btn" style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 10, fontSize: 11 }}
              disabled={!f.evidence_ids.length}
              onClick={() => r.setDrawer({ kind: 'evidence', title: FACTOR_LABEL[f.factor_id] ?? f.factor_id, ids: f.evidence_ids })}>
              <span>{FACTOR_LABEL[f.factor_id] ?? f.factor_id}</span>
              <span className="mono" style={{ color: f.risk === null ? C.unk : C.text }}>{f.risk === null ? 'Unknown' : fmtRatioPct(f.risk, 0)}</span>
            </button>
          ))}
        </div>
      </details>
    </div>
  )
}

function MarketPanel({ sel }: { sel: PositionView }) {
  const r = useRadar()
  const p = sel.pendle?.pendle
  const m = sel.morpho?.morpho
  const ids = [...(sel.morpho?.evidence_ids ?? []), ...(sel.pendle?.evidence_ids ?? []), ...sel.strategy.evidence_ids]
  const util = num(m?.utilization)
  return (
    <div className="panel stack gap-6 pad">
      <div className="row between gap-10 wrap">
        <Kicker>MARKET</Kicker>
        <button type="button" className="btn--link" style={{ fontSize: 11 }} onClick={() => r.setDrawer({ kind: 'evidence', title: 'Market sources', ids: [...new Set(ids)] })}>Sources</button>
      </div>
      <ListRow k={`${sel.ptName} price`} v={m?.collateral_price?.value ? `${fmtFixed(m.collateral_price.value, 4)} USD` : UNAVAILABLE} unknown={!m?.collateral_price?.value} mono />
      <ListRow k="Morpho oracle price" v={m?.oracle_price?.value ? `${fmtFixed(m.oracle_price.value, 4)} ${sel.loanName}` : UNAVAILABLE} unknown={!m?.oracle_price?.value} mono />
      <ListRow k="PT implied APY" v={fmtRatioPct(p?.implied_apy, 2)} unknown={!p?.implied_apy} mono />
      <ListRow k="Underlying APY" v={fmtRatioPct(p?.underlying_apy, 2)} unknown={!p?.underlying_apy} mono />
      <ListRow k="Pendle liquidity" v={fmtUsd(p?.liquidity_usd)} unknown={!p?.liquidity_usd} mono />
      <ListRow k="Morpho utilization" v={fmtRatioPct(m?.utilization, 1)} unknown={util === null} mono vColor={util !== null && util > 0.9 ? C.cau : undefined} />
      <ListRow k="Borrow rate" v={fmtRatioPct(m?.borrow_apy, 2)} unknown={!m?.borrow_apy} mono />
      <ListRow k="Maturity" v={fmtDate(sel.maturity)} mono />
    </div>
  )
}
