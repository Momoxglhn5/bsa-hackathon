"""Structural reachability is exposure, not a quantified failure or loss."""
from decimal import Decimal, localcontext
from typing import Literal

import networkx as nx

from app.analytics.valuation import value_position
from app.graph.builder import as_networkx
from app.models.evidence import ContractModel, PreciseDecimal, EvidenceId
from app.graph.types import Truth


class ExposedPosition(ContractModel):
    position_id: str
    kind: str
    path: tuple[str, ...]
    collateral_value_usd: PreciseDecimal | None
    equity_usd: PreciseDecimal | None
    link_statuses: tuple[Truth, ...]
    evidence_ids: tuple[EvidenceId, ...]


class DependencyExposure(ContractModel):
    dependency_id: str
    positions: tuple[ExposedPosition, ...]
    unique_position_count: int
    collateral_exposure_usd: PreciseDecimal | None
    basis: Literal['STRUCTURAL_REACHABILITY_NOT_LOSS']
    warnings: tuple[str, ...]


def dependency_exposure(snapshot, graph, node_id, *, now=None):
    g = as_networkx(graph)
    if node_id not in g:
        raise KeyError('Unknown dependency')
    positions = []
    with localcontext() as ctx:
        ctx.prec = 60
        for p in sorted(snapshot.positions, key=lambda p: p.position_id):
            if p.position_id not in g or not nx.has_path(g, p.position_id, node_id):
                continue
            path = nx.shortest_path(g, p.position_id, node_id)
            edges = [e for a, b in zip(path, path[1:]) for e in g[a][b]['relations']]
            v = value_position(snapshot, p, now=now or snapshot.as_of)
            positions.append({'position_id': p.position_id, 'kind': p.kind.value,
                'path': path, 'collateral_value_usd': str(v.collateral_value_usd) if v.collateral_value_usd is not None else None,
                'equity_usd': str(v.equity_usd) if v.equity_usd is not None else None,
                'link_statuses': sorted({e.verification_status for e in edges}),
                'evidence_ids': sorted({i for e in edges for i in e.evidence_ids})})
        total = (str(sum((Decimal(p['collateral_value_usd']) for p in positions), Decimal(0)))
                 if all(p['collateral_value_usd'] is not None for p in positions) else None)
    return {'dependency_id': node_id, 'positions': positions,
            'unique_position_count': len(positions), 'collateral_exposure_usd': total,
            'basis': 'STRUCTURAL_REACHABILITY_NOT_LOSS',
            'warnings': ['Alternative routes and unresolved links may occur in paths.',
                         'Simulated positions are hypothetical; this is not a realized loss.']}


def exposure(snapshot, position_ids, field="equity_usd", *, now=None):
    """Unique root positions; any missing contribution keeps aggregate unknown."""
    values = []
    for pid in sorted(set(position_ids)):
        p = next(p for p in snapshot.positions if p.position_id == pid)
        value = getattr(value_position(snapshot, p, now=now), field)
        if value is None:
            return None
        # Insolvency remains visible in Valuation. Capital at risk cannot be negative.
        values.append(max(Decimal(0), value) if field == "equity_usd" else value)
    return sum(values, Decimal(0))
