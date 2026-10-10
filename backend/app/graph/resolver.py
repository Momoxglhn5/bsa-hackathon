"""Translate explicit Data relationships without inferring economic backing.

SY assetInfo is an accounting denomination; yieldToken is a contract dependency.
Neither is evidence for a BACKED_BY relation.
"""
from app.graph.types import Edge, Node

MAPPING = {
    "MARKET_LOAN_ASSET": ("USES_LOAN_ASSET", ("ECONOMIC", "LIVENESS"), True),
    "MARKET_COLLATERAL_ASSET": ("COLLATERALIZED_BY", ("ECONOMIC", "LIVENESS", "EXECUTION"), True),
    "MARKET_ORACLE": ("USES_ORACLE", ("VALUATION", "LIVENESS"), True),
    "MARKET_IRM": ("USES_IRM", ("ECONOMIC", "LIVENESS"), True),
    "PENDLE_MARKET_PT": ("USES_PT", ("EXECUTION",), False),
    "PENDLE_MARKET_SY": ("USES_SY", ("ECONOMIC", "LIVENESS", "EXECUTION"), True),
    "PENDLE_MARKET_YT": ("USES_YT", ("EXECUTION",), False),
    "SY_YIELD_TOKEN": ("USES_YIELD_TOKEN", ("LIVENESS", "EXECUTION"), True),
    "SY_ACCOUNTING_ASSET": ("ACCOUNTED_IN", ("ECONOMIC",), False),
    "ECONOMIC_BACKING": ("BACKED_BY", ("ECONOMIC", "LIVENESS"), True),
}


def resolve_relations(snapshot, existing_nodes):
    from app.graph.builder import rpc_evidence
    aliases = {}
    types = {}
    for m in snapshot.markets:
        if m.morpho:
            for address, typ in ((m.morpho.oracle_address, "ORACLE"), (m.morpho.irm_address, "IRM")):
                aliases[f"contract:{m.chain_id}:{address}"] = f"{typ.lower()}:{m.chain_id}:{address}"
        if m.pendle:
            types[m.pendle.sy.asset_id] = "SY"
    nodes, edges = {}, []
    for r in snapshot.relations:
        if any(int(id.split(":")[1]) != r.chain_id for id in (r.source_node_id, r.target_node_id)):
            raise ValueError("relation node chain differs from its proof chain")
        rel, modes, required = MAPPING[r.relation_type.value]
        source, target = (aliases.get(x, x) for x in (r.source_node_id, r.target_node_id))
        truth = {"ONCHAIN_VERIFIED": "CONFIRMED", "API_OBSERVED": "OBSERVED",
                 "CURATED": "HYPOTHESIS", "UNRESOLVED": "UNRESOLVED"}[r.verification_status.value]
        records = {e.evidence_id: e for e in snapshot.evidence}
        raw_target = r.source_node_id.split(":")[-1]
        # Morpho params are read on the singleton, rather than the market bytes32.
        market = next((m for m in snapshot.markets if m.market_key == r.source_node_id), None)
        address = market.morpho.morpho_address if market and market.morpho else raw_target
        if truth == "CONFIRMED" and (r.block_number is None or r.observed_at > snapshot.as_of
                or not rpc_evidence(snapshot, r.evidence_ids, chain_id=r.chain_id,
                                    block=r.block_number, target=address)):
            truth = "UNRESOLVED"
        if truth == "OBSERVED" and (not r.evidence_ids or any(
                i not in records or records[i].data_mode != r.data_mode or records[i].chain_id != r.chain_id
                or records[i].status in {"MISSING", "ERROR", "MISMATCH"}
                or records[i].fetched_at > snapshot.as_of for i in r.evidence_ids)):
            truth = "UNRESOLVED"
        if r.data_mode == "DEMO" and truth in {"CONFIRMED", "OBSERVED"}:
            truth = "HYPOTHESIS"
        for id in (source, target):
            if id in existing_nodes:
                continue
            prefix = id.split(":")[0]
            typ = types.get(id, {"morpho": "MARKET", "pendle": "MARKET", "asset": "ASSET",
                                 "oracle": "ORACLE", "irm": "IRM"}.get(prefix, "CONTRACT"))
            nodes[id] = Node(id=id, type=typ, label=id, chain_id=r.chain_id)
        edges.append(Edge(id=r.relation_id, source=source, target=target, relation_type=rel,
                          verification_status=truth, evidence_ids=r.evidence_ids,
                          failure_modes=modes, required=required, as_of=r.observed_at,
                          block_number=r.block_number))
    return nodes, edges
