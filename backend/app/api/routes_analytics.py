from decimal import localcontext
from typing import Generic, TypeVar

from fastapi import APIRouter, Depends, Query
from app.models.evidence import DataMode
from pydantic import BaseModel, Field

from app.analytics.dataset import AnalyticsSnapshot
from app.analytics.service import AnalyticsService
from app.graph.builder import build_graph
from app.graph.types import GraphDTO
from app.graph.exposure import DependencyExposure, dependency_exposure
from app.models.evidence import ContractModel, EvidenceDTO, UtcDatetime
from app.risk.scoring import RiskResult, current_risk
from app.stress.schemas import SimulationRequest, SimulationResult
from app.decision.schemas import DecisionRequest, DecisionResult
from app.decision.carry import CarryAssumptions, CarryResult, estimate_carry
from app.decision.closing import ClosingAssumptions, ClosingPlanResult, closing_plan

from app.stress.monte_carlo.adapter import (
    MonteCarloDataRequest, MonteCarloDataUnavailable, MonteCarloNotApplicable,
    build_monte_carlo_request, unavailable_result,
)
from app.stress.monte_carlo.models import MonteCarloResult
from app.stress.monte_carlo import run_monte_carlo

T = TypeVar("T")


class Provenance(ContractModel):
    snapshot_id: str
    data_modes: tuple[str, ...]
    position_kinds: tuple[str, ...]
    replay: bool
    evaluated_at: UtcDatetime
    evidence: tuple[EvidenceDTO, ...]


class Envelope(BaseModel, Generic[T]):
    schema_version: str = "analytics-v1.1"
    status: str
    as_of: UtcDatetime
    data: T
    provenance: Provenance
    missing_fields: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class ScenarioCatalog(ContractModel):
    scenario_types: tuple[str, ...]
    probability_basis: str = "USER_ASSUMPTION"


class IncidentFrequencies(ContractModel):
    estimates: tuple[str, ...] = ()
    methodology: str = "No calibrated history or denominator supplied; enter explicit horizon-specific user assumptions."


class CarryRequest(ContractModel):
    strategy_id: str
    position_id: str
    horizon_days: int = Field(default=7, strict=True, ge=1, le=365)
    assumptions: CarryAssumptions | None = None


class ClosingPlanRequest(ContractModel):
    strategy_id: str
    position_id: str
    exit_quote_id: str | None = None
    assumptions: ClosingAssumptions | None = None


def create_router(service=None, *, provider=None):
    router = APIRouter(prefix="/api/v1", tags=["Core analytics"])

    def resolve(data_mode: DataMode = Query(DataMode.REAL)):
        return service if service is not None else provider.get(data_mode)

    def resolve_catalog():
        if service is not None:
            return service
        return AnalyticsService(AnalyticsSnapshot(snapshot_id="catalog:no-market-data",
            as_of=provider.clock(), description="Static scenario definitions; no market observations",
            markets=(), positions=(), strategies=(), evidence=()), replay=False, clock=provider.clock)

    def envelope(service, data, status="OK"):
        snapshot = service.snapshot
        modes = tuple(sorted({m.data_mode.value for m in (*snapshot.markets, *snapshot.positions, *snapshot.evidence)}))
        warnings = list(getattr(data, "warnings", ()))
        if "DEMO" in modes:
            warnings.append("SYNTHETIC_DATA: not on-chain observations")
        if service.replay:
            warnings.append("FROZEN_REPLAY: freshness evaluated at snapshot time, not a live quote")
        if not any(s.status == "VERIFIED_ACTIVE" for s in snapshot.strategies):
            warnings.append("NO_VERIFIED_LIVE_STRATEGY")
        if any(p.kind == "SIMULATED" for p in snapshot.positions):
            warnings.append("HYPOTHETICAL_SIZE: simulated holdings; borrowing capacity and market depth are not guaranteed")
        missing = tuple(k for k, v in data.model_dump().items() if v is None)
        return Envelope(status=status, as_of=snapshot.as_of, data=data,
                        provenance=Provenance(snapshot_id=snapshot.snapshot_id, data_modes=modes,
                                              position_kinds=tuple(sorted({p.kind.value for p in snapshot.positions})),
                                              replay=service.replay, evaluated_at=service.now(), evidence=snapshot.evidence),
                        missing_fields=missing, warnings=tuple(warnings))

    @router.get("/graph/{strategy_id}", response_model=Envelope[GraphDTO], summary="Dependencies and supporting evidence")
    def graph(strategy_id: str, service=Depends(resolve)):
        result = build_graph(service.snapshot, strategy_id)
        return envelope(service, result, result.graph_status)

    @router.get('/graph/{strategy_id}/exposure', response_model=Envelope[DependencyExposure])
    def exposure(strategy_id: str, node_id: str = Query(...), service=Depends(resolve)):
        result = dependency_exposure(service.snapshot, build_graph(service.snapshot, strategy_id),
                                     node_id, now=service.now())
        return envelope(service, DependencyExposure.model_validate(result), 'STRUCTURAL_EXPOSURE')

    @router.get("/risk/{strategy_id}", response_model=Envelope[RiskResult], summary="Observable factors, coverage and evidence confidence")
    def risk(strategy_id: str, service=Depends(resolve)):
        result = service.risk(strategy_id)
        return envelope(service, result, result.risk_status)

    @router.get("/scenarios", response_model=Envelope[ScenarioCatalog])
    def scenarios(service=Depends(resolve_catalog)):
        return envelope(service, ScenarioCatalog(scenario_types=("ORACLE_MANIPULATION", "ORACLE_OUTAGE", "UNAUTHORIZED_MINTING", "CROSS_CHAIN_FAILURE")))

    @router.post("/simulations", response_model=Envelope[SimulationResult], summary="Run a hypothetical failure; never execute transactions")
    def simulations(request: SimulationRequest, service=Depends(resolve)):
        result = service.simulate(request)
        return envelope(service, result, result.scenario_status)

    @router.post("/monte-carlo", response_model=Envelope[MonteCarloResult],
                 summary="Oracle-price Monte Carlo on Data inputs; volatility assumed or calibrated on the oracle series")
    def monte_carlo(request: MonteCarloDataRequest, service=Depends(resolve)):
        snapshot = service.snapshot
        strategy = snapshot.strategy(request.strategy_id)
        position = snapshot.position(request.strategy_id, request.position_id)
        now = service.now()
        try:
            with localcontext() as ctx:
                ctx.prec = 60
                mapped = build_monte_carlo_request(
                    position, snapshot.market(position), strategy, settings=request,
                    as_of=now, evidence=snapshot.evidence, series=snapshot.series,
                    replay=service.replay,
                )
        except MonteCarloNotApplicable as exc:
            result = exc.result
        except MonteCarloDataUnavailable as exc:
            # Business status, not a transport error: nothing is simulated or substituted.
            result = unavailable_result(request, now, exc.missing_fields)
        else:
            result = run_monte_carlo(mapped)
        response = envelope(service, result, result.status.value)
        response.missing_fields = tuple(result.missing_fields)
        return response

    @router.get("/incident-frequencies", response_model=Envelope[IncidentFrequencies])
    def incidents(service=Depends(resolve_catalog)):
        return envelope(service, IncidentFrequencies(), "INSUFFICIENT_DATA")

    @router.post("/carry-estimates", response_model=Envelope[CarryResult])
    def carry_estimate(request: CarryRequest, service=Depends(resolve)):
        result = estimate_carry(service.snapshot, request.strategy_id, request.position_id,
                                request.horizon_days, service.now(), request.assumptions)
        response = envelope(service, result, result.status)
        response.missing_fields = result.missing_fields
        return response

    @router.post("/closing-plans", response_model=Envelope[ClosingPlanResult])
    def closing_preview(request: ClosingPlanRequest, service=Depends(resolve)):
        result = closing_plan(service.snapshot, request.strategy_id, request.position_id,
                              request.exit_quote_id, service.now(), request.assumptions)
        response = envelope(service, result, result.status)
        response.missing_fields = result.missing_fields
        return response

    @router.post("/decisions", response_model=Envelope[DecisionResult], summary="Conditional STAY/EXIT and break-even, no ML required")
    def decisions(request: DecisionRequest, service=Depends(resolve)):
        result = service.decide(request)
        return envelope(service, result, result.decision_status)

    return router
