import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { SOURCES } from '../api/client'
import { getDataDelivery, getDataStatus, getEvidence, getHealth, getIncidentCatalog, getStrategies } from '../api/endpoints'
import type { EvidenceDTO } from '../api/types'
import { Kicker, Seg, Spinner, Tag } from '../components/ui'
import { fmtDate, fmtDateTime, fmtInt, shortAddr } from '../lib/format'
import { C } from '../lib/labels'
import { strategyPair } from '../lib/model'
import { useRadar } from '../state/radar'
import { useLoad } from '../state/useRequest'

export function DrawerHost() {
  const r = useRadar()
  const d = r.drawer
  const setDrawer = r.setDrawer
  const open = d !== null
  const panelRef = useRef<HTMLElement>(null)
  // Focus moves into the drawer on open and returns to the opener on close.
  useEffect(() => {
    if (!open) return
    const prev = document.activeElement as HTMLElement | null
    panelRef.current?.querySelector<HTMLElement>('button')?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setDrawer(null) }
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey); prev?.focus?.() }
  }, [open, setDrawer])
  if (!d) return null
  const head = {
    data: { kicker: 'DATA', title: 'Sources and freshness' },
    method: { kicker: 'METHODOLOGY', title: 'Engine calculations' },
    incidents: { kicker: 'INCIDENTS', title: 'DeFi history' },
    evidence: { kicker: 'EVIDENCE', title: d.kind === 'evidence' ? d.title : '' },
  }[d.kind]
  return (
    <>
      <div className="overlay" onClick={() => r.setDrawer(null)} aria-hidden="true" />
      <aside ref={panelRef} className="drawer" data-scroll="1" role="dialog" aria-modal="true" aria-label={head.title}>
        <div className="row between gap-10" style={{ alignItems: 'flex-start' }}>
          <div className="stack gap-4">
            <Kicker gold>{head.kicker}</Kicker>
            <span className="serif" style={{ fontSize: 26, lineHeight: 1.1, overflowWrap: 'anywhere' }}>{head.title}</span>
          </div>
          <button type="button" className="btn btn--icon" style={{ background: 'transparent' }} aria-label="Close" onClick={() => r.setDrawer(null)}>✕</button>
        </div>
        {d.kind === 'data' && <DataDrawer />}
        {d.kind === 'method' && <MethodDrawer />}
        {d.kind === 'incidents' && <IncidentsDrawer />}
        {d.kind === 'evidence' && <EvidenceDrawer ids={d.ids} />}
      </aside>
    </>
  )
}

function Block({ k, tag, tagColor = C.unk, children }: { k: string; tag?: string; tagColor?: string; children: ReactNode }) {
  return (
    <div className="drawer-block">
      <div className="drawer-block-head"><span>{k}</span>{tag && <Tag color={tagColor}>{tag}</Tag>}</div>
      <div className="drawer-block-body">{children}</div>
    </div>
  )
}

function Line({ k, v, mono, color }: { k: ReactNode; v: ReactNode; mono?: boolean; color?: string }) {
  return (
    <div className="row between gap-10">
      <span>{k}</span>
      <span className={mono ? 'mono' : undefined} style={{ color: color ?? C.textSoft, textAlign: 'right', fontSize: mono ? 10.5 : undefined }}>{v}</span>
    </div>
  )
}

const COUNT_LABEL: Record<string, string> = {
  evidence: 'Evidence', markets: 'Markets', strategies: 'Strategies', relations: 'Relations', positions: 'Positions', raw_snapshots: 'Raw snapshots',
}

function DataDrawer() {
  const r = useRadar()
  const prov = r.loaded.catalog?.provenance
  const sel = r.sel
  const key = `${r.source.id}|${sel?.strategyId ?? ''}|${r.reloadedAt ?? 0}`
  const health = useLoad(key, (s) => getHealth(r.source, { signal: s }))
  const strategies = useLoad(key, (s) => getStrategies(r.source, { signal: s }))
  const status = useLoad(sel ? key : null, (s) => getDataStatus(r.source, sel!.strategyId, { signal: s }))
  const delivery = useLoad(key, (s) => getDataDelivery(r.source, { signal: s }))
  const fresh = status.data?.data.freshness
  const del = delivery.data

  return (
    <>
      <Seg label="Dataset" value={r.source.id} onChange={(id) => r.setSourceId(id)} options={SOURCES.map((s) => ({ id: s.id, label: s.label }))} />
      <Block k="Snapshot" tag={prov?.replay ? 'REPLAY' : 'LIVE'} tagColor={prov?.replay ? C.cau : C.pos}>
        {prov ? (
          <div className="stack gap-4">
            <Line k="Mode" v={prov.data_modes.join(' · ') || '—'} mono />
            <Line k="Evaluated at" v={fmtDateTime(prov.evaluated_at)} mono />
            <Line k="Identifier" v={prov.snapshot_id} mono />
          </div>
        ) : 'Catalog not loaded.'}
      </Block>
      <Block k="Database" tag={health.data?.status ?? (health.error ? 'UNAVAILABLE' : undefined)} tagColor={health.data?.status === 'OK' ? C.pos : C.cau}>
        {health.loading && <Spinner label="Reading…" />}
        {health.error}
        {health.data && (
          <div className="stack gap-4">
            {Object.entries(health.data.data.counts).filter(([, v]) => v > 0).map(([k, v]) => <Line key={k} k={COUNT_LABEL[k] ?? k} v={fmtInt(v)} mono />)}
          </div>
        )}
      </Block>
      {(fresh || status.error) && (
        <Block k="Freshness" tag={fresh ? (fresh.is_stale ? 'STALE' : 'UP TO DATE') : 'UNAVAILABLE'} tagColor={fresh && !fresh.is_stale ? C.pos : C.cau}>
          {status.error}
          {fresh && status.data && (
            <div className="stack gap-4">
              <Line k="Last read" v={fmtDateTime(fresh.as_of)} mono />
              <Line k="Age / validity" v={`${Math.round(fresh.age_seconds / 60)} min / ${Math.round(fresh.ttl_seconds / 3600)} h`} mono />
              <Line k="Evidence" v={fmtInt(status.data.data.evidence_count)} mono />
            </div>
          )}
        </Block>
      )}
      <Block k="Strategies" tag={strategies.data ? String(strategies.data.data.length) : strategies.error ? 'UNAVAILABLE' : undefined}>
        {strategies.error}
        <div className="stack gap-4">
          {strategies.data?.data.map((s) => <Line key={s.strategy_id} k={strategyPair(s).pair} v={`matures ${fmtDate(s.expiry)}`} mono />)}
        </div>
      </Block>
      <Block k="Positions" tag={String(r.positions.length)}>
        <div className="stack gap-4">
          {r.positions.map((p) => <Line key={p.id} k={`${p.title} · ${p.pair}`} v={p.quotes.length ? `${p.quotes.length} quote${p.quotes.length > 1 ? 's' : ''}` : 'no quote'} mono />)}
        </div>
      </Block>
      {del && (
        <Block k="Data delivery" tag={del.status} tagColor={C.pos}>
          <Line k="Published at" v={fmtDateTime(del.as_of)} mono />
        </Block>
      )}
    </>
  )
}

function MethodDrawer() {
  const items: [string, string][] = [
    ['Risk index', '0–100 index over ten factors across five dimensions: liquidity, security, governance, operational history, dependencies. Published when at least 50% of factors and three dimensions are documented.'],
    ['Coverage and confidence', 'Coverage: share of documented factors. Confidence: quality and freshness of sources.'],
    ['Graph', 'A → B: A depends on B. Solid line: confirmed on-chain; dashes: observed via API or hypothesis; dots: unresolved.'],
    ['Shock', 'The shock hits the targeted dependency and propagates along the graph to the positions. Quantified loss = assumed haircut × affected collateral.'],
    ['Monte Carlo', 'Oracle price simulated as geometric Brownian motion. Output: LLTV breach frequency, LTV quantiles, 95% VaR and ES.'],
    ['Carry', 'PT return over the horizon minus debt cost: V × ((1 + r)^(d/365) − 1) − D × ((1 + APY)^(d/365) − 1).'],
    ['Closing', 'Repay the debt, withdraw the PT, sell the exact quoted quantity, recover the funding. Exit cost = spot value − net proceeds + gas and fees.'],
    ['STAY / EXIT', 'Stay = equity + carry − Σ probability × loss. Exit = equity − exit cost. The break-even is the probability where both are equal.'],
    ['Predictive', 'Carry is replaced by a validated forecast when one exists for the position and horizon.'],
  ]
  return <>{items.map(([k, v]) => <Block key={k} k={k}>{v}</Block>)}</>
}

function IncidentsDrawer() {
  const r = useRadar()
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<'ALL' | 'ECO' | 'MKT'>('ECO')
  const catalog = useLoad(`${r.source.id}`, (s) => getIncidentCatalog(r.source, { signal: s }))
  const events = catalog.data?.data.events ?? []
  const list = useMemo(() => {
    const q = query.trim().toLowerCase()
    return events
      .filter((e) => filter === 'ALL' || (filter === 'ECO' ? e.related_ecosystem : e.matches_selected_market === true))
      .filter((e) => !q || `${e.name} ${e.technique ?? ''} ${e.classification ?? ''}`.toLowerCase().includes(q))
      .sort((a, b) => b.date.localeCompare(a.date))
      .slice(0, 80)
  }, [events, query, filter])
  return (
    <>
      <input className="input" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search incidents" aria-label="Search incidents" />
      <Seg label="Filter" value={filter} onChange={setFilter} options={[{ id: 'ECO', label: 'Related ecosystem' }, { id: 'MKT', label: 'This market' }, { id: 'ALL', label: 'All' }]} />
      {catalog.loading && <Spinner label="Loading…" />}
      {catalog.error && <Block k="Catalog unavailable" tag={catalog.apiError?.status === 503 ? '503' : 'ERROR'} tagColor={C.dan}>{catalog.error}</Block>}
      {catalog.data && (
        <span className="kicker">{fmtInt(events.length)} INCIDENTS · SOURCE DEFILLAMA · {fmtDate(catalog.data.data.retrieved_at)}</span>
      )}
      {catalog.data && list.length === 0 && <Block k="No incident">No incident for this filter.</Block>}
      {list.map((e) => (
        <Block key={`${e.name}-${e.date}`} k={`${e.name} · ${fmtDate(e.date)}`} tag={filter === 'ALL' && e.related_ecosystem ? 'RELATED' : undefined} tagColor={C.cau}>
          {[e.classification, e.technique].filter(Boolean).join(' · ') || '—'}
          {e.reported_loss_usd != null && ` · ${fmtInt(e.reported_loss_usd)} USD`}
          {(e.source_url ?? e.catalog_url) && <> · <a href={(e.source_url ?? e.catalog_url)!} target="_blank" rel="noopener noreferrer">source</a></>}
        </Block>
      ))}
    </>
  )
}

function EvidenceDrawer({ ids }: { ids: string[] }) {
  const r = useRadar()
  const known = ids.map((id) => r.evidence.get(id)).filter((e): e is EvidenceDTO => !!e)
  const missing = ids.filter((id) => !r.evidence.has(id))
  const [fetched, setFetched] = useState<Record<string, EvidenceDTO | string>>({})
  useEffect(() => {
    const ctrl = new AbortController()
    missing.forEach((id) => {
      getEvidence(r.source, id, { signal: ctrl.signal })
        .then((env) => setFetched((f) => ({ ...f, [id]: env.data })))
        .catch((err) => { if (!ctrl.signal.aborted) setFetched((f) => ({ ...f, [id]: err?.message ?? 'Evidence not found' })) })
    })
    return () => ctrl.abort()
  }, [ids.join(','), r.source])
  const all = [...known, ...missing.map((id) => fetched[id]).filter((e): e is EvidenceDTO => typeof e === 'object')]
  const failed = missing.filter((id) => typeof fetched[id] === 'string')
  return (
    <>
      {ids.length === 0 && <Block k="No evidence">No evidence attached.</Block>}
      {all.map((e) => (
        <Block key={e.evidence_id} k={`${e.source_kind} · ${e.method}`} tag={e.status} tagColor={e.status === 'OK' ? C.pos : C.cau}>
          <div className="stack gap-4">
            <Line k="Source" v={e.source_id} mono />
            <Line k="Read at" v={fmtDateTime(e.fetched_at)} mono />
            {e.block_number ? <Line k="Block" v={fmtInt(e.block_number)} mono /> : null}
            {e.target_address && (
              <Line k="Address" mono v={(
                <button type="button" className="btn--link mono" style={{ fontSize: 10.5 }} title="Copy address" onClick={() => navigator.clipboard?.writeText(e.target_address!)}>{shortAddr(e.target_address)}</button>
              )} />
            )}
            {e.payload_sha256 && <Line k="Hash" v={`sha256 ${shortAddr(e.payload_sha256)}`} mono />}
            {e.error_message && <Line k="Error" v={e.error_message} color={C.cau} />}
            {e.source_uri && <a href={e.source_uri} target="_blank" rel="noopener noreferrer">Open source</a>}
          </div>
        </Block>
      ))}
      {missing.some((id) => fetched[id] === undefined) && <Spinner label="Loading evidence…" />}
      {failed.length > 0 && <Block k={`${failed.length} evidence item(s) not found`} tag="UNAVAILABLE" tagColor={C.cau}>{failed.join(', ')}</Block>}
    </>
  )
}
