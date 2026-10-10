"""Deterministic graph: A -> B means A depends on B."""
import hashlib

import networkx as nx

from app.analytics.dataset import AnalyticsSnapshot
from app.graph.types import Edge, GraphDTO, Node


def rpc_evidence(snapshot, ids, *, chain_id=1, block=None, target=None):
    evidence = {e.evidence_id: e for e in snapshot.evidence}
    return bool(ids) and all(
        (e := evidence.get(i)) is not None and e.data_mode == "REAL"
        and e.source_kind == "RPC" and e.status == "OK"
        and e.chain_id == chain_id and e.block_number is not None
        and e.fetched_at <= snapshot.as_of
        and (block is None or e.block_number == block)
        and (target is None or e.target_address == target)
        for i in ids
    )


def relation_truth(snapshot, market, check_name, expected):
    if market.data_mode == "DEMO":
        return "HYPOTHESIS", market.evidence_ids
    for check in market.verification:
        if check.check == check_name:
            if (market.block_number is not None and market.status not in {"ERROR", "MISMATCH", "STALE"}
                    and check.status == "VERIFIED" and check.onchain_value == expected
                    and rpc_evidence(snapshot, check.evidence_ids, chain_id=market.chain_id,
                                     block=market.block_number,
                                     target=market.morpho.morpho_address if market.morpho else market.market_id)):
                return "CONFIRMED", check.evidence_ids
            if check.status in {"MISMATCH", "ERROR"}:
                return "UNRESOLVED", check.evidence_ids
    records = {e.evidence_id: e for e in snapshot.evidence}
    observed = any((e := records.get(i)) is not None and e.source_kind in {"MORPHO_API", "PENDLE_API"}
                   and e.status in {"OK", "PARTIAL", "UNVERIFIED"} for i in market.evidence_ids)
    return ("OBSERVED" if observed else "UNRESOLVED"), market.evidence_ids


def build_graph(snapshot: AnalyticsSnapshot, strategy_id: str, *, hypothetical=False) -> GraphDTO:
    strategy = snapshot.strategy(strategy_id)
    nodes, edges = {}, {}

    def node(id, type, label=None, **kwargs):
        nodes.setdefault(id, Node(id=id, type=type, label=label or id, **kwargs))
        return id

    def edge(source, target, relation, truth, evidence=(), modes=("ECONOMIC",), block=None):
        id = "edge:" + hashlib.sha256(f"{source}|{target}|{relation}".encode()).hexdigest()[:24]
        edges[id] = Edge(id=id, source=source, target=target, relation_type=relation,
                         verification_status=truth, evidence_ids=evidence,
                         failure_modes=modes, as_of=snapshot.as_of, block_number=block)

    morpho = next(m for m in snapshot.markets if m.market_key == f"morpho:{strategy.chain_id}:{strategy.morpho_market_id}")
    mf = morpho.morpho
    mid = node(morpho.market_key, "MARKET")
    for target, typ, rel, check, expected, modes in (
        (mf.loan_token.asset_id, "ASSET", "USES_LOAN_ASSET", "morpho.loan_token", mf.loan_token.address, ("ECONOMIC", "LIVENESS")),
        (mf.collateral_token.asset_id, "ASSET", "COLLATERALIZED_BY", "morpho.collateral_token", mf.collateral_token.address, ("ECONOMIC", "LIVENESS", "EXECUTION")),
        (f"oracle:{morpho.chain_id}:{mf.oracle_address}", "ORACLE", "USES_ORACLE", "morpho.oracle", mf.oracle_address, ("VALUATION", "LIVENESS")),
        (f"irm:{morpho.chain_id}:{mf.irm_address}", "IRM", "USES_IRM", "morpho.irm", mf.irm_address, ("ECONOMIC", "LIVENESS")),
    ):
        truth, ev = relation_truth(snapshot, morpho, check, expected)
        edge(mid, node(target, typ), rel, truth, ev, modes, morpho.block_number)

    for p in sorted(snapshot.positions, key=lambda p: p.position_id):
        if p.strategy_id != strategy_id:
            continue
        pid = node(p.position_id, "POSITION", p.label, position_kind=p.kind.value)
        # A simulated holding is an explicit hypothesis even on a verified market.
        truth = "CONFIRMED" if p.kind == "ONCHAIN" and p.block_number is not None and rpc_evidence(snapshot, p.evidence_ids, chain_id=p.chain_id, block=p.block_number, target=mf.morpho_address) else "HYPOTHESIS"
        edge(pid, mid, "BORROWS_FROM", truth, p.evidence_ids,
             ("ECONOMIC", "LIVENESS", "VALUATION", "EXECUTION"), p.block_number)
        edge(pid, mf.collateral_token.asset_id, "COLLATERALIZED_BY", truth, p.evidence_ids,
             ("ECONOMIC", "LIVENESS", "EXECUTION"), p.block_number)

    warnings = []
    pendle = next((m for m in snapshot.markets if m.pendle and m.chain_id == strategy.chain_id
                   and m.market_id == strategy.pendle_market_address
                   and m.pendle.pt.same_asset(mf.collateral_token)), None)
    if pendle:
        pm = node(pendle.market_key, "MARKET")
        truth, ev = relation_truth(snapshot, pendle, "pendle.pt", pendle.pendle.pt.address)
        edge(mf.collateral_token.asset_id, pm, "REDEEMS_THROUGH", truth, ev,
             ("ECONOMIC", "LIVENESS", "EXECUTION"), pendle.block_number)
        truth, ev = relation_truth(snapshot, pendle, "pendle.sy", pendle.pendle.sy.address)
        edge(pm, node(pendle.pendle.sy.asset_id, "SY"), "USES_SY", truth, ev,
             ("ECONOMIC", "LIVENESS", "EXECUTION"), pendle.block_number)
        # assetInfo alone does not prove economic backing. Do not infer BACKED_BY.
    else:
        warnings.append("PENDLE_JOIN_UNRESOLVED: exact PT address and chain match required")

    from app.graph.resolver import resolve_relations
    resolved_nodes, resolved_edges = resolve_relations(snapshot, nodes)
    nodes.update(resolved_nodes)
    for resolved in resolved_edges:
        same = [id for id, e in edges.items() if (e.source, e.target, e.relation_type) ==
                (resolved.source, resolved.target, resolved.relation_type)]
        # Data relations are the authoritative record, including unresolved/mismatch.
        for id in same:
            del edges[id]
        edges[resolved.id] = resolved

    # Explicit supplementary dependencies supplied with evidence by Data/resolvers.
    extras = {n.id: n for n in snapshot.additional_nodes}
    for e in snapshot.additional_edges:
        if e.verification_status == "HYPOTHESIS" and not hypothetical:
            continue
        truth = e.verification_status
        if truth == "CONFIRMED" and (e.block_number is None or e.as_of > snapshot.as_of
                                     or not rpc_evidence(snapshot, e.evidence_ids, block=e.block_number)):
            truth = "UNRESOLVED"
        for id in (e.source, e.target):
            if id in extras:
                if id in nodes and nodes[id] != extras[id]:
                    raise ValueError("supplementary node conflicts with a Data node")
                nodes[id] = extras[id]
        if e.source not in nodes or e.target not in nodes:
            raise ValueError("supplementary edge refers to unknown node")
        if e.id in edges:
            raise ValueError("supplementary edge conflicts with a generated edge")
        edges[e.id] = e.model_copy(update={"verification_status": truth})

    # Keep only dependencies reachable from this strategy, not unrelated markets.
    g = nx.DiGraph()
    g.add_nodes_from(nodes)
    g.add_edges_from((e.source, e.target) for e in edges.values())
    keep = {mid} | nx.descendants(g, mid)
    for p in snapshot.positions:
        if p.strategy_id == strategy_id:
            keep |= {p.position_id} | nx.descendants(g, p.position_id)
    return GraphDTO(strategy_id=strategy_id, snapshot_as_of=snapshot.as_of,
                    nodes=tuple(nodes[k] for k in sorted(keep)),
                    edges=tuple(sorted((e for e in edges.values() if e.source in keep and e.target in keep), key=lambda e: e.id)),
                    warnings=tuple(warnings))


def as_networkx(graph: GraphDTO) -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_nodes_from(n.id for n in graph.nodes)
    # DiGraph holds multiple relationship semantics per pair without overwriting.
    for e in graph.edges:
        if not g.has_edge(e.source, e.target):
            g.add_edge(e.source, e.target, relations=[])
        g[e.source][e.target]["relations"].append(e)
    return g
