"""Published equal-weight heuristic. A Risk Index is not a probability."""
from datetime import timedelta
from decimal import Decimal

import networkx as nx
from pydantic import Field

from app.analytics.valuation import Valuation, value_position
from app.graph.builder import as_networkx, build_graph, rpc_evidence
from app.graph.exposure import exposure
from app.graph.propagation import propagate, healthy_fallback
from app.models.evidence import ContractModel, EvidenceId, PreciseDecimal, UnitRatio

FACTORS = {
    "L_UTILIZATION": "Liquidity", "L_TVL_DRAWDOWN": "Liquidity",
    "S_SOURCE_VERIFIED": "Security", "S_AUDIT": "Security",
    "G_CONTROL": "Governance", "G_TIMELOCK": "Governance",
    "O_DEPLOYMENT_AGE": "Operational history", "O_VERSION_AGE": "Operational history",
    "D_CONCENTRATION": "Dependencies", "D_NO_FALLBACK": "Dependencies",
}
QUALITY = {"RPC": Decimal("1"), "MORPHO_API": Decimal("0.8"),
           "PENDLE_API": Decimal("0.8"), "DERIVED": Decimal("0.5"),
           "USER_INPUT": Decimal("0.3"), "FIXTURE": Decimal("0")}


class Factor(ContractModel):
    factor_id: str
    dimension: str
    weight: PreciseDecimal = Field(default=Decimal("0.1"), ge=Decimal("0.1"), le=Decimal("0.1"))
    risk: UnitRatio | None = None
    source_quality: UnitRatio | None = None
    freshness: UnitRatio | None = None
    evidence_ids: tuple[EvidenceId, ...] = ()
    explanation: str


class DimensionScore(ContractModel):
    score: PreciseDecimal | None
    coverage_pct: PreciseDecimal


class RiskResult(ContractModel):
    risk_index: PreciseDecimal | None
    risk_status: str
    completeness_pct: PreciseDecimal
    evidence_confidence_pct: PreciseDecimal | None
    dimension_scores: dict[str, DimensionScore]
    factor_explanations: tuple[Factor, ...]
    unknown_factors: tuple[str, ...]
    position_risk: dict[str, Valuation] = Field(default_factory=dict)
    warnings: tuple[str, ...]


def score_factors(factors, position_risk=None):
    if {f.factor_id for f in factors} != set(FACTORS) or len(factors) != 10:
        raise ValueError("exactly the ten defined factors are required")
    if any(f.dimension != FACTORS[f.factor_id] for f in factors):
        raise ValueError("factor dimension must match its published definition")
    known = [f for f in factors if f.risk is not None]
    dimensions = {}
    for dim in dict.fromkeys(FACTORS.values()):
        values = [f.risk for f in known if f.dimension == dim]
        dimensions[dim] = DimensionScore(score=sum(values)/len(values)*100 if values else None,
                                         coverage_pct=Decimal(len(values))*50)
    completeness = Decimal(len(known))*10
    publish = completeness >= 50 and sum(d.coverage_pct >= 50 for d in dimensions.values()) >= 3
    confidence = sum((f.source_quality*f.freshness for f in known), Decimal(0))/len(known)*100 if known else None
    return RiskResult(risk_index=sum((f.risk for f in known), Decimal(0))/len(known)*100 if publish else None,
                      risk_status="AVAILABLE" if publish else "INSUFFICIENT_DATA",
                      completeness_pct=completeness, evidence_confidence_pct=confidence,
                      dimension_scores=dimensions, factor_explanations=tuple(factors),
                      unknown_factors=tuple(f.factor_id for f in factors if f.risk is None),
                      position_risk=position_risk or {},
                      warnings=("HEURISTIC_NOT_FAILURE_PROBABILITY", "CONFIDENCE_IS_EVIDENCE_QUALITY_NOT_SAFETY"))


def current_risk(snapshot, strategy_id, now):
    s = snapshot.strategy(strategy_id)
    market = next(m for m in snapshot.markets if m.market_key == f"morpho:{s.chain_id}:{s.morpho_market_id}")
    evidence = {e.evidence_id: e for e in snapshot.evidence}
    observations = {}
    for o in snapshot.risk_observations:
        if o.market_key == market.market_key:
            if o.factor_id not in FACTORS or o.factor_id in observations:
                raise ValueError("unknown or duplicate risk factor observation")
            observations[o.factor_id] = (o.risk, o.observed_at, o.evidence_ids, o.methodology)
    if market.morpho.utilization is not None and market.status not in {"ERROR", "MISMATCH", "MISSING"}:
        observations["L_UTILIZATION"] = (market.morpho.utilization, market.as_of, market.evidence_ids,
                                          "Linear liquidity-pressure heuristic: risk = Morpho utilization (0..1).")
    graph = build_graph(snapshot, strategy_id)
    positions = [p for p in snapshot.positions if p.strategy_id == strategy_id]
    total = exposure(snapshot, [p.position_id for p in positions])
    critical = [e for e in graph.edges if e.required and e.criticality == "CRITICAL"]
    if total is not None and total > 0 and critical:
        # These are concentration measures of the represented portfolio, not
        # assumptions about unknown underlying contracts or failure likelihoods.
        targets = sorted({e.target for e in critical})
        max_exposure = Decimal(0)
        for target in targets:
            impacted = set()
            for mode in {mode for e in critical if e.target == target for mode in e.failure_modes}:
                impact = propagate(snapshot, graph, [target], mode)
                impacted.update(impact.confirmed_position_ids + impact.potential_position_ids)
            capital = exposure(snapshot, impacted)
            if capital is not None:
                max_exposure = max(max_exposure, capital)
        refs = tuple(sorted({i for e in critical for i in e.evidence_ids}))
        observations.setdefault("D_CONCENTRATION", (max_exposure / total, snapshot.as_of, refs,
            "Largest unique equity exposure to a represented critical dependency / total strategy equity; incomplete graph coverage remains explicit."))
        g = as_networkx(graph)
        without_fallback = []
        for p in positions:
            reachable = {p.position_id} | nx.descendants(g, p.position_id)
            relevant = [e for e in critical if e.source in reachable and e.source != p.position_id]
            if any(not any(healthy_fallback(snapshot, f, e, now)
                           for f in e.fallbacks) for e in relevant):
                without_fallback.append(p.position_id)
        observations.setdefault("D_NO_FALLBACK", (exposure(snapshot, without_fallback)/total, snapshot.as_of, refs,
            "Share of equity with at least one represented critical dependency lacking a verified healthy fallback; not proof that no fallback exists."))
    # Full daily coverage, not two sparse endpoints masquerading as 30-day history.
    for series in snapshot.series:
        if series.subject_id not in {market.market_id, market.market_key} or series.metric != "LIQUIDITY_USD" or series.unit != "USD":
            continue
        points = [p for p in series.points if now-timedelta(days=31) <= p.timestamp <= now]
        if (len(points) >= 31 and points[0].timestamp <= now-timedelta(days=30)
                and points[-1].timestamp >= now-timedelta(days=1)
                and all(p.value is not None and p.value >= 0 and p.status not in {"ERROR", "MISSING", "MISMATCH"} for p in points)
                and all(b.timestamp-a.timestamp <= timedelta(hours=36) for a, b in zip(points, points[1:]))):
            peak = max(p.value for p in points)
            if peak > 0:
                observations["L_TVL_DRAWDOWN"] = ((peak-points[-1].value)/peak, points[-1].timestamp,
                    tuple(sorted({e for p in points for e in p.evidence_ids})), "30-day peak-to-current USD liquidity drawdown; daily coverage required.")
    factors = []
    for id, dim in FACTORS.items():
        observation = observations.get(id)
        if observation:
            risk, timestamp, ids, methodology = observation
            records = [evidence.get(i) for i in ids]
            if not ids or any(e is None or e.status in {"ERROR", "MISSING", "MISMATCH"}
                             or e.chain_id != market.chain_id or e.data_mode != market.data_mode
                             or e.fetched_at > now for e in records) or timestamp > now:
                observation = None
        if observation is None:
            factors.append(Factor(factor_id=id, dimension=dim, explanation="Unknown: missing usable evidence or applicable methodology; never counted as zero risk."))
            continue
        quality = min(QUALITY[e.source_kind.value] for e in records)
        oldest = min([timestamp] + [e.fetched_at for e in records])
        seconds = Decimal(str(max(0, (now-oldest).total_seconds())))
        freshness = max(Decimal(0), Decimal(1)-seconds/Decimal(86400))
        if any(e.status == "STALE" for e in records) or market.status == "STALE":
            freshness = min(freshness, Decimal("0.25"))
        factors.append(Factor(factor_id=id, dimension=dim, risk=risk, source_quality=quality,
                              freshness=freshness, evidence_ids=ids, explanation=methodology))
    positions = {p.position_id: value_position(snapshot, p, now=now) for p in snapshot.positions if p.strategy_id == strategy_id}
    return score_factors(factors, positions)
