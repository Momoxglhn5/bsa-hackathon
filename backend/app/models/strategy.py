"""Data contract v1.0 — strategies (Pendle PT used as collateral on Morpho Blue).

A strategy is a *verified relationship* between data objects, referenced by
market ids. It holds no yield spread, risk score or recommendation.
"""
from __future__ import annotations

import hashlib
from enum import Enum

from pydantic import Field, model_validator

from .evidence import (
    BlockNumber,
    Bytes32,
    DataStatus,
    EvidenceId,
    EvmAddress,
    SourceId,
    UtcDatetime,
    VerificationCheckDTO,
    VerificationStatus,
    VersionedModel,
    normalize_address,
    normalize_bytes32,
)
from .market import TokenRef


class StrategyType(str, Enum):
    PENDLE_PT_COLLATERAL_ON_MORPHO = "PENDLE_PT_COLLATERAL_ON_MORPHO"


class StrategyStatus(str, Enum):
    CANDIDATE = "CANDIDATE"              # discovered via API, not (fully) verified on-chain
    VERIFIED_ACTIVE = "VERIFIED_ACTIVE"  # relationships verified via RPC, PT not expired
    EXPIRED = "EXPIRED"                  # PT expiry reached
    UNSUPPORTED = "UNSUPPORTED"          # outside MVP scope or failed verification


def make_strategy_id(chain_id: int, morpho_market_id: str, pt_address: str) -> str:
    blob = f"{chain_id}:{normalize_bytes32(morpho_market_id)}:{normalize_address(pt_address)}"
    return "strat_" + hashlib.sha256(blob.encode()).hexdigest()[:24]


class StrategyDTO(VersionedModel):
    strategy_id: str = Field(pattern=r"^strat_[0-9a-f]{16,64}$")
    strategy_type: StrategyType = StrategyType.PENDLE_PT_COLLATERAL_ON_MORPHO
    status: StrategyStatus
    status_reason: str | None = Field(default=None, max_length=512)
    data_status: DataStatus

    morpho_market_id: Bytes32
    pendle_market_address: EvmAddress | None = None
    pt: TokenRef
    loan_asset: TokenRef
    underlying_asset: TokenRef | None = None
    expiry: UtcDatetime | None = None

    as_of: UtcDatetime
    block_number: BlockNumber | None = None
    verification: tuple[VerificationCheckDTO, ...] = ()
    source_ids: tuple[SourceId, ...] = Field(min_length=1)
    evidence_ids: tuple[EvidenceId, ...] = ()

    @model_validator(mode="after")
    def _consistency(self) -> "StrategyDTO":
        if self.pt.same_asset(self.loan_asset):
            raise ValueError("pt and loan_asset must differ")
        for token in (self.pt, self.loan_asset, self.underlying_asset):
            if token is not None and token.chain_id != self.chain_id:
                raise ValueError("token chain_id differs from strategy chain_id")

        check_statuses = {c.status for c in self.verification}
        has_mismatch = VerificationStatus.MISMATCH in check_statuses
        expired = self.expiry is not None and self.expiry <= self.as_of

        if expired and self.status not in (StrategyStatus.EXPIRED, StrategyStatus.UNSUPPORTED):
            raise ValueError("expiry reached: status must be EXPIRED or UNSUPPORTED")

        if self.status == StrategyStatus.VERIFIED_ACTIVE:
            if not self.verification or check_statuses != {VerificationStatus.VERIFIED}:
                raise ValueError("VERIFIED_ACTIVE requires all checks VERIFIED")
            missing = [
                name
                for name in ("pendle_market_address", "expiry", "block_number")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(f"VERIFIED_ACTIVE requires {missing}")
            if not self.evidence_ids:
                raise ValueError("VERIFIED_ACTIVE requires evidence_ids")
            if self.data_status not in (DataStatus.OK, DataStatus.PARTIAL):
                raise ValueError("VERIFIED_ACTIVE requires data_status OK or PARTIAL")
        elif self.status == StrategyStatus.CANDIDATE and self.data_status == DataStatus.OK:
            raise ValueError("CANDIDATE strategy is not verified: data_status cannot be OK")

        if self.status == StrategyStatus.EXPIRED and not expired:
            raise ValueError("EXPIRED requires expiry <= as_of")
        if self.status == StrategyStatus.UNSUPPORTED and not self.status_reason:
            raise ValueError("UNSUPPORTED requires status_reason")
        if has_mismatch and self.data_status not in (DataStatus.MISMATCH, DataStatus.ERROR):
            raise ValueError("verification mismatch requires data_status MISMATCH or ERROR")

        expected_id = make_strategy_id(self.chain_id, self.morpho_market_id, self.pt.address)
        if self.strategy_id != expected_id:
            raise ValueError("strategy_id must equal make_strategy_id(chain_id, market_id, pt)")
        return self