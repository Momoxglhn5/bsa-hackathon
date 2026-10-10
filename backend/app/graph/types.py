"""Analytics graph contracts; Data V1 models remain unchanged."""
from typing import Literal

from pydantic import Field

from app.models.evidence import ContractModel, EvidenceId, UtcDatetime

Truth = Literal["CONFIRMED", "OBSERVED", "HYPOTHESIS", "UNRESOLVED"]
FailureMode = Literal["VALUATION", "LIVENESS", "ECONOMIC", "CONTROL", "EXECUTION"]


class Node(ContractModel):
    id: str
    type: Literal["POSITION", "MARKET", "ASSET", "SY", "ORACLE", "IRM", "BRIDGE", "CHAIN", "PROTOCOL", "CONTRACT", "GOVERNANCE", "PRICE_FEED"]
    label: str
    chain_id: int = 1
    position_kind: str | None = None


class Fallback(ContractModel):
    node_id: str
    state: Literal["HEALTHY", "FAILED", "UNKNOWN"] = "UNKNOWN"
    evidence_ids: tuple[EvidenceId, ...] = ()


class Edge(ContractModel):
    id: str
    source: str
    target: str
    relation_type: Literal["BORROWS_FROM", "LENDS_TO", "USES_LOAN_ASSET", "COLLATERALIZED_BY", "USES_ORACLE", "USES_IRM", "USES_PT", "USES_SY", "BACKED_BY", "REDEEMS_THROUGH", "QUOTED_BY", "RUNS_ON", "BRIDGED_BY", "GOVERNED_BY", "USES_YT", "USES_YIELD_TOKEN", "ACCOUNTED_IN"]
    verification_status: Truth
    evidence_ids: tuple[EvidenceId, ...] = ()
    criticality: Literal["CRITICAL", "MATERIAL", "SUPPORTING"] = "CRITICAL"
    failure_modes: tuple[FailureMode, ...] = ("ECONOMIC",)
    required: bool = True
    fallbacks: tuple[Fallback, ...] = ()
    as_of: UtcDatetime
    block_number: int | None = None


class GraphDTO(ContractModel):
    strategy_id: str
    graph_status: Literal["PARTIAL", "VERIFIED"] = "PARTIAL"
    snapshot_as_of: UtcDatetime
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    unknown_dependency_classes: tuple[str, ...] = ("ORACLE_IMPLEMENTATION", "YIELD_SOURCE", "GOVERNANCE")
    warnings: tuple[str, ...] = ()


class Propagation(ContractModel):
    affected_node_ids: tuple[str, ...]
    confirmed_position_ids: tuple[str, ...]
    potential_position_ids: tuple[str, ...]
    representative_paths: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    evidence_ids: tuple[EvidenceId, ...] = ()
