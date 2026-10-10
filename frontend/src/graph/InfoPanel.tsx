import { useState } from 'react'
import type { ReactNode } from 'react'
import { useApi } from '../data/useApi'
import { useRadar } from '../data/useRadar'
import { api } from '../lib/api'
import { age, amount, num, pct, utc } from '../lib/format'
import type { GraphEdge, GraphNode } from '../lib/graph'
import type { Market } from '../lib/types'
import { Facts, KIND, Notes, SectionLabel, StatusBadge, VerificationBadge } from '../ui/primitives'
import Sparkline from '../ui/Sparkline'

export type PanelTarget = { type: 'node'; id: string } | { type: 'edge'; id: string }

interface Props {
  target: PanelTarget
  canGoBack: boolean
  onBack: () => void
  onClose: () => void
  onNavigate: (nodeId: string, viaEdge?: string) => void
}

function marketFor(node: GraphNode, markets: Market[]): Market | undefined {
  return markets.find((m) => `${m.protocol === 'MORPHO_BLUE' ? 'morpho' : 'pendle'}:1:${m.market_id.toLowerCase()}` === node.id)
}

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="flex flex-col gap-2.5 border-t border-line-soft px-4 py-4">
      <div className="flex items-center justify-between gap-2">
        <SectionLabel>{title}</SectionLabel>
        {aside}
      </div>
      {children}
    </div>
  )
}

function DependencyRow({ node, edge, onClick }: { node: GraphNode; edge: GraphEdge; onClick: () => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className="flex min-h-11 w-full cursor-pointer items-center gap-2.5 rounded-md border-0 bg-transparent px-2.5 py-2 text-left hover:bg-hover"
      >
        <span className="size-2 shrink-0 rounded-full" style={{ background: KIND[node.kind].color }} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-ink">{node.label}</span>
          <span className="mt-px block font-mono text-[10px] text-ink-4">
            {edge.type} · {edge.status}
          </span>
        </span>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#94A3B8" strokeWidth="2" aria-hidden="true">
          <path d="M9 6l6 6-6 6" />
        </svg>
      </button>
    </li>
  )
}

function EvidenceRow({ id }: { id: string }) {
  const [open, setOpen] = useState(false)
  const result = useApi(open ? id : null, (s) => api.evidence(id, s))
  return (
    <li className="rounded-md border border-line">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full cursor-pointer items-center justify-between gap-2 border-0 bg-transparent px-2.5 py-2 text-left font-mono text-[11px] text-ink-2 hover:bg-hover"
      >
        {id}
        <span aria-hidden="true">{open ? '−' : '+'}</span>
      </button>
      {open && result && (
        <div className="border-t border-line-soft px-2.5 py-2">
          {result.state === 'loading' && <p className="m-0 text-[11px] text-ink-3">Loading GET /evidence/{'{id}'}…</p>}
          {result.state === 'error' && <p className="m-0 text-[11px] text-bad">{result.error.message}</p>}
          {result.state === 'ready' && (
            <Facts
              rows={[
                { label: 'Status', value: <StatusBadge status={result.value.data.status} /> },
                { label: 'Source', value: `${result.value.data.source_kind} · ${result.value.data.source_id}` },
                { label: 'Method', value: result.value.data.method },
                { label: 'Block', value: result.value.data.block_number ?? 'none', missing: result.value.data.block_number === null },
                { label: 'Fetched', value: utc(result.value.data.fetched_at) },
                { label: 'Payload hash', value: result.value.data.payload_sha256?.slice(0, 14) ?? 'none', missing: !result.value.data.payload_sha256 },
              ]}
            />
          )}
        </div>
      )}
    </li>
  )
}

function Metrics({ node, market }: { node: GraphNode; market: Market | undefined }) {
  const { detail, collateralPrice } = useRadar()
  const position = useRadar().position?.data
  if (node.kind === 'market' && market?.morpho) {
    const m = market.morpho
    const loan = m.loan_token.symbol
    return (
      <Facts
        rows={[
          { label: 'LLTV', value: pct(num(m.lltv), 1) },
          { label: 'Utilization', value: pct(num(m.utilization), 1), missing: m.utilization === null },
          { label: 'Supply APY', value: pct(num(m.supply_apy)), missing: m.supply_apy === null },
          { label: 'Borrow APY', value: pct(num(m.borrow_apy)), missing: m.borrow_apy === null },
          { label: 'Total supply', value: amount(num(m.total_supply_assets), loan), missing: m.total_supply_assets === null },
          { label: 'Total borrow', value: amount(num(m.total_borrow_assets), loan), missing: m.total_borrow_assets === null },
          { label: 'Oracle price', value: m.oracle_price ?? 'Unavailable', missing: m.oracle_price === null },
        ]}
      />
    )
  }
  if (node.kind === 'pool' && market?.pendle) {
    const p = market.pendle
    return (
      <Facts
        rows={[
          { label: 'Expiry', value: utc(p.expiry).slice(0, 10) },
          { label: 'Implied APY', value: pct(num(p.implied_apy)), missing: p.implied_apy === null },
          { label: 'Underlying APY', value: pct(num(p.underlying_apy)), missing: p.underlying_apy === null },
          { label: 'Total PT', value: amount(num(p.total_pt)), missing: p.total_pt === null },
          { label: 'Total SY', value: amount(num(p.total_sy)), missing: p.total_sy === null },
          { label: 'Liquidity', value: amount(num(p.liquidity_usd), 'USD'), missing: p.liquidity_usd === null },
        ]}
      />
    )
  }
  if (node.kind === 'position' && position) {
    return (
      <Facts
        rows={[
          { label: 'Kind', value: position.kind },
          { label: 'Collateral', value: amount(num(position.collateral_amount), position.collateral_asset.symbol, 2) },
          { label: 'Borrow', value: amount(num(position.borrow_amount), position.loan_asset.symbol, 2) },
          { label: 'Owner', value: position.owner ?? 'none (simulated)', missing: !position.owner },
        ]}
      />
    )
  }
  const isPt = node.address === detail.data.strategy.pt.address.toLowerCase()
  const last = collateralPrice?.data.points.at(-1)
  return (
    <Facts
      rows={[
        { label: 'Address', value: node.address ?? 'none' },
        ...(isPt && last ? [{ label: 'Market price (last)', value: last.value ?? 'Unavailable', missing: last.value === null }] : []),
      ]}
    />
  )
}

function Freshness({ entityId }: { entityId: string }) {
  const result = useApi(entityId, (s) => api.dataStatus(entityId, s))
  if (!result || result.state === 'loading') return <p className="m-0 text-[11px] text-ink-3">Loading GET /data-status…</p>
  if (result.state === 'error') return <p className="m-0 text-[11px] text-bad">{result.error.message}</p>
  const f = result.value.data.freshness
  return (
    <Facts
      rows={[
        { label: 'Status', value: <StatusBadge status={result.value.status} /> },
        { label: 'Age / TTL', value: `${age(f.age_seconds)} / ${age(f.ttl_seconds)}` },
        { label: 'Evidence records', value: result.value.data.evidence_count },
      ]}
    />
  )
}

function SeriesBlock({ entityId, metric, unit }: { entityId: string; metric: string; unit: 'pct' | 'price' }) {
  const result = useApi(`${entityId}|${metric}`, (s) => api.series(entityId, metric, s))
  if (!result || result.state === 'loading') return <p className="m-0 text-[11px] text-ink-3">Loading {metric}…</p>
  if (result.state === 'error') {
    return <p className="m-0 text-[11px] text-ink-4 italic">{result.error.kind === 'not_found' ? `No ${metric} series stored.` : result.error.message}</p>
  }
  return <Sparkline series={result.value.data} format={unit === 'pct' ? (v) => pct(v) : (v) => v.toFixed(4)} />
}

function NodeBody({ node, onNavigate }: { node: GraphNode; onNavigate: Props['onNavigate'] }) {
  const { graph, markets, detail } = useRadar()
  const market = marketFor(node, markets.map((m) => m.data))
  const marketEnvelope = markets.find((m) => m.data === market)
  const out = graph.outgoing.get(node.id) ?? []
  const inc = graph.incoming.get(node.id) ?? []
  const evidence = [...new Set([...(market?.evidence_ids ?? []), ...out.flatMap((e) => e.evidenceIds), ...inc.flatMap((e) => e.evidenceIds)])]
  const isPt = node.address === detail.data.strategy.pt.address.toLowerCase()
  return (
    <>
      <Section title="Metrics" aside={marketEnvelope ? <StatusBadge status={marketEnvelope.status} /> : undefined}>
        <Metrics node={node} market={market} />
        {marketEnvelope && <Notes items={[...marketEnvelope.missing_fields.map((f) => `missing: ${f}`), ...marketEnvelope.warnings]} />}
      </Section>
      {market && (
        <Section title="Freshness">
          <Freshness entityId={node.id} />
        </Section>
      )}
      {(node.kind === 'pool' || isPt) && (
        <Section title={node.kind === 'pool' ? 'PT implied APY' : 'PT market price'}>
          {node.kind === 'pool' ? (
            <SeriesBlock entityId={node.id} metric="PT_IMPLIED_APY" unit="pct" />
          ) : (
            <SeriesBlock entityId={node.id} metric="MARKET_PRICE" unit="price" />
          )}
        </Section>
      )}
      <Section title={`Depends on · ${out.length}`}>
        {out.length ? (
          <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
            {out.map((e) => (
              <DependencyRow key={e.id} edge={e} node={graph.byId.get(e.target)!} onClick={() => onNavigate(e.target, e.id)} />
            ))}
          </ul>
        ) : (
          <p className="m-0 text-xs text-ink-4">No dependency recorded.</p>
        )}
      </Section>
      <Section title={`Used by · ${inc.length}`}>
        {inc.length ? (
          <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
            {inc.map((e) => (
              <DependencyRow key={e.id} edge={e} node={graph.byId.get(e.source)!} onClick={() => onNavigate(e.source, e.id)} />
            ))}
          </ul>
        ) : (
          <p className="m-0 text-xs text-ink-4">Nothing in this strategy depends on it.</p>
        )}
      </Section>
      <Section title={`Evidence · ${evidence.length}`}>
        {evidence.length ? (
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {evidence.map((id) => (
              <EvidenceRow key={id} id={id} />
            ))}
          </ul>
        ) : (
          <p className="m-0 text-xs text-ink-4">No evidence record: this link is joined by the UI.</p>
        )}
      </Section>
    </>
  )
}

function EdgeBody({ edge, onNavigate }: { edge: GraphEdge; onNavigate: Props['onNavigate'] }) {
  const { graph } = useRadar()
  const source = graph.byId.get(edge.source)!
  const target = graph.byId.get(edge.target)!
  return (
    <>
      <Section title="Relation">
        <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
          <DependencyRow node={source} edge={edge} onClick={() => onNavigate(source.id, edge.id)} />
          <DependencyRow node={target} edge={edge} onClick={() => onNavigate(target.id, edge.id)} />
        </ul>
        <p className="m-0 text-xs leading-relaxed text-ink-3">
          <span className="text-ink">{source.label}</span> depends on <span className="text-ink">{target.label}</span>.
        </p>
        {edge.note && <Notes items={[edge.note]} />}
      </Section>
      <Section title={`Evidence · ${edge.evidenceIds.length}`}>
        {edge.evidenceIds.length ? (
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {edge.evidenceIds.map((id) => (
              <EvidenceRow key={id} id={id} />
            ))}
          </ul>
        ) : (
          <p className="m-0 text-xs text-ink-4">No evidence record: this link is joined by the UI.</p>
        )}
      </Section>
    </>
  )
}

export default function InfoPanel({ target, canGoBack, onBack, onClose, onNavigate }: Props) {
  const { graph } = useRadar()
  const node = target.type === 'node' ? graph.byId.get(target.id) : undefined
  const edge = target.type === 'edge' ? graph.edges.find((e) => e.id === target.id) : undefined
  if (!node && !edge) return null
  const kind = node ? KIND[node.kind] : null
  return (
    <aside
      aria-label={node ? `Details for ${node.label}` : 'Relation details'}
      className="absolute inset-y-0 right-0 z-10 flex w-full flex-col overflow-y-auto border-l border-line bg-card shadow-[-12px_0_32px_rgba(0,0,0,.35)] sm:w-[420px]"
    >
      <div className="sticky top-0 z-10 flex flex-col gap-2 border-b border-line-soft bg-card px-4 pt-3 pb-4">
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={onBack}
            disabled={!canGoBack}
            className="h-8 cursor-pointer rounded-md border border-[#243049] bg-control px-2.5 text-xs text-ink-2 hover:bg-hover disabled:cursor-not-allowed disabled:opacity-40"
          >
            ← Back
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close details"
            className="flex size-8 cursor-pointer items-center justify-center rounded-md border border-[#243049] bg-control text-ink-2 hover:bg-hover"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
        <div className="flex items-center gap-1.5 text-[11px] text-ink-3">
          <span className="size-2 rounded-full" style={{ background: kind?.color ?? '#f3f4f6' }} />
          {node ? kind?.label : 'Relation'}
        </div>
        <div className="text-base font-semibold break-words">{node ? node.label : edge!.type}</div>
        <div className="flex flex-wrap items-center gap-2">
          <VerificationBadge status={node ? node.verification : edge!.status} />
          {node?.address && <span className="font-mono text-[11px] break-all text-ink-4">{node.address}</span>}
        </div>
      </div>
      {node && <NodeBody node={node} onNavigate={onNavigate} />}
      {edge && <EdgeBody edge={edge} onNavigate={onNavigate} />}
    </aside>
  )
}
