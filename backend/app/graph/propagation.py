"""Reverse worklist propagation, including failures of fallback dependencies.

A node advances at most twice: unaffected -> potential -> confirmed. A fallback
change requeues the blocked primary edge, so traversal order cannot hide its loss.
"""
from collections import defaultdict, deque
from datetime import timedelta

from app.graph.builder import as_networkx, rpc_evidence
from app.graph.types import GraphDTO, Propagation


def healthy_fallback(snapshot, fallback, edge, now=None):
    now = now or snapshot.as_of
    records = {e.evidence_id: e for e in snapshot.evidence}
    return (fallback.state == "HEALTHY"
            and rpc_evidence(snapshot, fallback.evidence_ids, block=edge.block_number)
            and all(now-timedelta(minutes=5) <= records[i].fetched_at <= now for i in fallback.evidence_ids))


def propagate(snapshot, graph: GraphDTO, failed_node_ids, failure_mode="ECONOMIC", *, now=None) -> Propagation:
    g = as_networkx(graph)
    failed = set(failed_node_ids)
    if not failed <= set(g):
        raise KeyError("Unknown target node")
    states = {n: 2 for n in failed}
    parent = {(n, 2): None for n in failed}
    queue = deque(sorted(failed))
    watchers = defaultdict(set)
    for edge in graph.edges:
        if edge.required and failure_mode in edge.failure_modes:
            for fallback in edge.fallbacks:
                watchers[fallback.node_id].add(edge.target)
    evidence = set()
    while queue:
        target = queue.popleft()
        severity = states[target]
        for source in sorted(g.predecessors(target)):
            for edge in g[source][target]["relations"]:
                if not edge.required or failure_mode not in edge.failure_modes:
                    continue
                usable, uncertain = False, False
                for fallback in edge.fallbacks:
                    fallback_severity = states.get(fallback.node_id, 0)
                    evidence.update(fallback.evidence_ids)
                    if fallback.state == "FAILED" or fallback_severity == 2:
                        continue
                    if fallback_severity == 0 and healthy_fallback(snapshot, fallback, edge, now):
                        usable = True
                    else:
                        uncertain = True
                if usable:
                    continue
                next_severity = 2 if severity == 2 and edge.verification_status == "CONFIRMED" and not uncertain else 1
                evidence.update(edge.evidence_ids)
                if next_severity > states.get(source, 0):
                    states[source] = next_severity
                    parent[(source, next_severity)] = (target, severity)
                    queue.append(source)
                    queue.extend(n for n in sorted(watchers[source]) if n in states)
    positions = {n.id for n in graph.nodes if n.type == "POSITION"}
    confirmed_ids = positions & {n for n, severity in states.items() if severity == 2}
    potential_ids = (positions & states.keys()) - confirmed_ids
    paths = {}
    for pid in sorted(confirmed_ids | potential_ids):
        state, path = (pid, states[pid]), []
        while state is not None:
            path.append(state[0])
            state = parent[state]
        paths[pid] = tuple(path)
    return Propagation(affected_node_ids=tuple(sorted(states)),
                       confirmed_position_ids=tuple(sorted(confirmed_ids)),
                       potential_position_ids=tuple(sorted(potential_ids)),
                       representative_paths=paths, evidence_ids=tuple(sorted(evidence)))
