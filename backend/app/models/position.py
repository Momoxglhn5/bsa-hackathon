"""Data contract v1.0 — positions on Morpho Blue markets.

Positions carry amounts only. Health factor, LTV and any risk metric are
computed downstream and are deliberately absent from this contract.
"""
from __future__ import annotations

from enum import Enum
from typing import ClassVar, Literal

from pydantic import Field, model_validator

from .evidence import (
    BlockNumber,
    Bytes32,
    DataStatus,
    EvidenceId,
    EvmAddress,
    NonNegativeDecimal,
    RawUint,
    SourceId,
    UtcDatetime,
    VersionedModel,
    enforce_status,
    missing_fields,
    scale_raw,
)
from .market import Protocol, TokenRef


class PositionKind(str, Enum):
    ONCHAIN = "ONCHAIN"      # read from Morpho `position(id, user)` via RPC
    SIMULATED = "SIMULATED"  # entered by the user / scenario, not read on-chain


class PositionDTO(VersionedModel):
    """Borrow position: collateral deposited, loan token borrowed.

    Amounts are in whole token units; raw on-chain values kept alongside.
    """

    CRITICAL_FIELDS: ClassVar[tuple[str, ...]] = ("collateral_amount", "borrow_amount")

    position_id: str = Field(pattern=r"^pos_[0-9a-z_\-]{4,64}$")
    kind: PositionKind
    protocol: Literal[Protocol.MORPHO_BLUE] = Protocol.MORPHO_BLUE
    market_id: Bytes32
    owner: EvmAddress | None = None
    collateral_asset: TokenRef
    loan_asset: TokenRef

    collateral_amount: NonNegativeDecimal | None = None
    collateral_amount_raw: RawUint | None = None
    borrow_amount: NonNegativeDecimal | None = None
    borrow_amount_raw: RawUint | None = None
    borrow_shares_raw: RawUint | None = None
    supply_shares_raw: RawUint | None = None

    strategy_id: str | None = None
    label: str | None = Field(default=None, max_length=128)
    as_of: UtcDatetime
    block_number: BlockNumber | None = None
    status: DataStatus
    source_ids: tuple[SourceId, ...] = Field(min_length=1)
    evidence_ids: tuple[EvidenceId, ...] = ()

    @model_validator(mode="after")
    def _consistency(self) -> "PositionDTO":
        if self.collateral_asset.same_asset(self.loan_asset):
            raise ValueError("collateral_asset and loan_asset must differ")
        for token in (self.collateral_asset, self.loan_asset):
            if token.chain_id != self.chain_id:
                raise ValueError("token chain_id differs from position chain_id")

        if self.kind == PositionKind.ONCHAIN:
            if self.owner is None:
                raise ValueError("ONCHAIN position requires owner")
            if self.block_number is None:
                raise ValueError("ONCHAIN position requires block_number")
            if not self.evidence_ids:
                raise ValueError("ONCHAIN position requires evidence_ids")

        for amount, raw, token in (
            ("collateral_amount", "collateral_amount_raw", self.collateral_asset),
            ("borrow_amount", "borrow_amount_raw", self.loan_asset),
        ):
            value, raw_value = getattr(self, amount), getattr(self, raw)
            if value is not None and raw_value is not None and token.decimals is not None:
                if value != scale_raw(raw_value, token.decimals):
                    raise ValueError(f"{amount} inconsistent with {raw}")

        enforce_status(
            self.status,
            missing_critical=missing_fields(self, self.CRITICAL_FIELDS),
            missing_metrics=[],
            label=self.position_id,
        )
        return self