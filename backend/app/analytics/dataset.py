"""Small snapshot boundary consuming actual shared Data V1 DTOs.

Snapshots are injected by the data developer or read from JSON, never fetched
by the analytics engines. Quote context adds missing closing semantics without
redefining ExitPriceDTO.
"""
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from app.graph.types import Edge, Node
from app.decision.forecast import ForecastBundle
from app.models.evidence import (ContractModel, EvidenceDTO, EvidenceId, RelationDTO,
                                 NonNegativeDecimal, UnitRatio, UtcDatetime)
from app.models.market import ExitPriceDTO, MarketDTO, MarketPriceDTO
from app.models.position import PositionDTO
from app.models.strategy import StrategyDTO
from app.models.timeseries import TimeseriesDTO


class QuoteContext(ContractModel):
    quote_id: str
    position_id: str
    quote: ExitPriceDTO
    output_usd_price: MarketPriceDTO | None = None
    gas_cost_usd: NonNegativeDecimal | None = None
    extra_fees_usd: NonNegativeDecimal | None = None
    route_available: bool | None = None
    closing_feasible: bool | None = None
    requires_external_repayment_liquidity: bool = True
    repayment_liquidity_confirmed: bool = False
    # These assertions belong to Data/the closing-plan provider, not a spot rate.
    basis: Literal["VERIFIED_PLAN", "USER_ASSUMPTION"]
    evidence_ids: tuple[EvidenceId, ...] = ()


class RiskObservation(ContractModel):
    market_key: str
    factor_id: str
    risk: UnitRatio
    observed_at: UtcDatetime
    evidence_ids: tuple[EvidenceId, ...] = Field(min_length=1)
    methodology: str = Field(min_length=1)


class AnalyticsInputs(ContractModel):
    """Optional supplied engine inputs, never synthesized from marginal spot rates."""
    quotes: tuple[QuoteContext, ...] = ()
    risk_observations: tuple[RiskObservation, ...] = ()
    forecasts: tuple[ForecastBundle, ...] = ()


class AnalyticsSnapshot(ContractModel):
    schema_version: Literal["analytics-snapshot-v1"] = "analytics-snapshot-v1"
    snapshot_id: str
    as_of: UtcDatetime
    description: str
    markets: tuple[MarketDTO, ...]
    positions: tuple[PositionDTO, ...]
    strategies: tuple[StrategyDTO, ...]
    evidence: tuple[EvidenceDTO, ...]
    relations: tuple[RelationDTO, ...] = ()
    series: tuple[TimeseriesDTO, ...] = ()
    quotes: tuple[QuoteContext, ...] = ()
    forecasts: tuple[ForecastBundle, ...] = ()
    risk_observations: tuple[RiskObservation, ...] = ()
    additional_nodes: tuple[Node, ...] = ()
    additional_edges: tuple[Edge, ...] = ()

    @model_validator(mode="after")
    def unique_and_linked(self):
        for objects, attr in ((self.markets, "market_key"), (self.positions, "position_id"),
                              (self.strategies, "strategy_id"), (self.evidence, "evidence_id"),
                              (self.quotes, "quote_id"), (self.additional_nodes, "id"),
                              (self.additional_edges, "id")):
            ids = [getattr(x, attr) for x in objects]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {attr}")
        market_index = {m.market_key: m for m in self.markets}
        strategies = {s.strategy_id: s for s in self.strategies}
        for p in self.positions:
            s = strategies.get(p.strategy_id)
            m = market_index.get(f"morpho:{p.chain_id}:{p.market_id}")
            if s is None or m is None or m.morpho is None:
                raise ValueError("position requires a known strategy and Morpho market")
            if (s.morpho_market_id != p.market_id or s.chain_id != p.chain_id
                    or not p.collateral_asset.same_asset(m.morpho.collateral_token)
                    or not p.loan_asset.same_asset(m.morpho.loan_token)):
                raise ValueError("position/strategy/market assets or chain mismatch")
        for s in self.strategies:
            m = market_index.get(f"morpho:{s.chain_id}:{s.morpho_market_id}")
            if m is None or m.morpho is None or not s.pt.same_asset(m.morpho.collateral_token):
                raise ValueError("strategy collateral must match the Morpho market")
            if not s.loan_asset.same_asset(m.morpho.loan_token):
                raise ValueError("strategy loan asset mismatch")
            pendle = next((p for p in self.markets if p.pendle and p.chain_id == s.chain_id
                           and p.market_id == s.pendle_market_address), None)
            if pendle is not None and pendle.data_mode != m.data_mode:
                raise ValueError("a strategy cannot join REAL and DEMO markets")
        if any(m.as_of > self.as_of for m in self.markets) or any(p.as_of > self.as_of for p in self.positions):
            raise ValueError("snapshot cannot precede its markets or positions")
        return self

    def strategy(self, strategy_id: str) -> StrategyDTO:
        return next((s for s in self.strategies if s.strategy_id == strategy_id), None) or _missing("strategy")

    def position(self, strategy_id: str, position_id: str) -> PositionDTO:
        self.strategy(strategy_id)
        return next((p for p in self.positions if p.position_id == position_id and p.strategy_id == strategy_id), None) or _missing("position")

    def market(self, position: PositionDTO) -> MarketDTO:
        return next(m for m in self.markets if m.market_key == f"morpho:{position.chain_id}:{position.market_id}")


def _missing(kind: str):
    raise KeyError(f"Unknown {kind}")


def load_snapshot(path: str | Path) -> AnalyticsSnapshot:
    import json
    data = json.loads(Path(path).read_text())
    if "generated_at" in data and "snapshot_id" not in data:
        data = {"schema_version": "analytics-snapshot-v1", "snapshot_id": "data-fixture:" + Path(path).stem,
                "as_of": data["generated_at"], "description": data.get("notice", "Data JSON fixture"),
                **{key: data.get(key, []) for key in ("markets", "strategies", "positions", "evidence", "relations", "series")}}
    return AnalyticsSnapshot.model_validate(data)
