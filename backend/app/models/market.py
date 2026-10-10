"""Data contract v1.0 — tokens, prices and markets (Morpho Blue / Pendle V2).

Three price types are kept strictly apart and are not interchangeable:
  * MarketPriceDTO  — reference/spot price from an API
  * OraclePriceDTO  — Morpho market oracle `price()` read on-chain
  * ExitPriceDTO    — size-dependent execution quote
"""
from __future__ import annotations

from enum import Enum
from typing import ClassVar, Literal, NamedTuple

from pydantic import Field, StrictBool, StrictInt, model_validator

from .evidence import (
    BlockNumber,
    Bytes32,
    ChainScopedModel,
    ContractModel,
    DataStatus,
    EvidenceId,
    EvmAddress,
    NonNegativeDecimal,
    PositiveDecimal,
    RawUint,
    Ratio,
    SourceId,
    TokenDecimals,
    UnitRatio,
    UtcDatetime,
    VerificationCheckDTO,
    VersionedModel,
    enforce_status,
    missing_fields,
    normalize_address,
    normalize_bytes32,
    scale_raw,
)

MORPHO_ORACLE_PRICE_SCALE = 36
WAD_DECIMALS = 18


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

class AssetKey(NamedTuple):
    chain_id: int
    address: str


class TokenRef(ChainScopedModel):
    """Token identified by (chain_id, address). `symbol` is a label only."""

    address: EvmAddress
    symbol: str | None = None
    name: str | None = None
    decimals: TokenDecimals | None = None

    @property
    def key(self) -> AssetKey:
        return AssetKey(self.chain_id, self.address)

    @property
    def asset_id(self) -> str:
        """`asset:<chain>:<lowercase address>`."""
        return f"asset:{self.chain_id}:{self.address}"

    def same_asset(self, other: "TokenRef") -> bool:
        return self.key == other.key


def _same(a: TokenRef | None, b: TokenRef | None) -> bool:
    return a is not None and b is not None and a.key == b.key


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

class QuoteUnit(str, Enum):
    USD = "USD"
    TOKEN = "TOKEN"  # quoted in `quote_asset`


class PricingMethod(str, Enum):
    API_SPOT = "API_SPOT"                # spot/reference price from an API
    PENDLE_IMPLIED = "PENDLE_IMPLIED"    # PT price derived by Pendle from the implied rate
    MORPHO_ORACLE = "MORPHO_ORACLE"      # IOracle.price() of a Morpho market, via RPC
    EXECUTION_QUOTE = "EXECUTION_QUOTE"  # executable swap quote for a given size


_PRICE_FORBIDDEN_STATUSES = frozenset({DataStatus.MISSING, DataStatus.ERROR})


class _PriceBase(ChainScopedModel):
    """Price of 1 whole unit of `base_asset` expressed in the quote unit.

    A missing price is represented by `None` on the parent field, never by a
    price object with a placeholder value.
    """

    ALLOWED_METHODS: ClassVar[frozenset[PricingMethod]] = frozenset()

    value: NonNegativeDecimal
    base_asset: TokenRef
    quote_unit: QuoteUnit
    quote_asset: TokenRef | None = None
    pricing_method: PricingMethod
    observed_at: UtcDatetime
    block_number: BlockNumber | None = None
    source_id: SourceId
    evidence_ids: tuple[EvidenceId, ...] = Field(min_length=1)
    status: DataStatus

    @model_validator(mode="after")
    def _base_consistency(self):
        if self.pricing_method not in self.ALLOWED_METHODS:
            raise ValueError(
                f"{type(self).__name__}: pricing_method {self.pricing_method.value} not allowed"
            )
        if (self.quote_unit == QuoteUnit.TOKEN) != (self.quote_asset is not None):
            raise ValueError("quote_asset is required iff quote_unit == TOKEN")
        if self.status in _PRICE_FORBIDDEN_STATUSES:
            raise ValueError("price object cannot be MISSING/ERROR; set the parent field to None")
        if _same(self.base_asset, self.quote_asset):
            raise ValueError("base_asset and quote_asset must differ")
        return self


class MarketPriceDTO(_PriceBase):
    ALLOWED_METHODS: ClassVar[frozenset[PricingMethod]] = frozenset(
        {PricingMethod.API_SPOT, PricingMethod.PENDLE_IMPLIED}
    )
    kind: Literal["MARKET"] = "MARKET"


class OraclePriceDTO(_PriceBase):
    """Morpho oracle price: collateral (base) quoted in loan token (quote).

    value == raw_value / 10**scale_exponent,
    scale_exponent == 36 + loan_decimals - collateral_decimals.
    """

    ALLOWED_METHODS: ClassVar[frozenset[PricingMethod]] = frozenset(
        {PricingMethod.MORPHO_ORACLE}
    )
    kind: Literal["ORACLE"] = "ORACLE"
    oracle_address: EvmAddress
    raw_value: RawUint
    scale_exponent: StrictInt

    @model_validator(mode="after")
    def _oracle_consistency(self) -> "OraclePriceDTO":
        if self.quote_unit != QuoteUnit.TOKEN:
            raise ValueError("oracle price must be quoted in the loan token")
        if self.block_number is None:
            raise ValueError("oracle price requires block_number")
        base_dec = self.base_asset.decimals
        quote_dec = self.quote_asset.decimals if self.quote_asset else None
        if base_dec is not None and quote_dec is not None:
            expected = MORPHO_ORACLE_PRICE_SCALE + quote_dec - base_dec
            if self.scale_exponent != expected:
                raise ValueError(f"scale_exponent {self.scale_exponent} != expected {expected}")
        if self.value != scale_raw(self.raw_value, self.scale_exponent):
            raise ValueError("value inconsistent with raw_value / 10**scale_exponent")
        return self


class ExitPriceDTO(_PriceBase):
    """Execution quote for selling `input_amount` of base_asset into quote_asset."""

    ALLOWED_METHODS: ClassVar[frozenset[PricingMethod]] = frozenset(
        {PricingMethod.EXECUTION_QUOTE}
    )
    kind: Literal["EXIT"] = "EXIT"
    input_amount: PositiveDecimal
    output_amount: NonNegativeDecimal
    venue: str = Field(min_length=1, max_length=64)
    price_impact: Ratio | None = None
    valid_until: UtcDatetime | None = None

    @model_validator(mode="after")
    def _exit_consistency(self) -> "ExitPriceDTO":
        if self.quote_unit != QuoteUnit.TOKEN:
            raise ValueError("exit price must be quoted in the received token")
        tolerance = self.output_amount.scaleb(-18)
        if abs(self.value * self.input_amount - self.output_amount) > tolerance:
            raise ValueError("value inconsistent with output_amount / input_amount")
        return self


# ---------------------------------------------------------------------------
# Markets
# ---------------------------------------------------------------------------

class Protocol(str, Enum):
    MORPHO_BLUE = "MORPHO_BLUE"
    PENDLE_V2 = "PENDLE_V2"


class MorphoMarketFields(ContractModel):
    """Morpho Blue market. Params are required: a market without params is not created.

    Token amounts are in whole token units (raw / 10**decimals); raw values kept alongside.
    """

    METRIC_FIELDS: ClassVar[tuple[str, ...]] = (
        "total_supply_assets",
        "total_borrow_assets",
        "utilization",
        "supply_apy",
        "borrow_apy",
        "fee",
        "oracle_price",
        "collateral_price",
        "loan_price",
    )

    morpho_address: EvmAddress
    loan_token: TokenRef
    collateral_token: TokenRef
    oracle_address: EvmAddress
    irm_address: EvmAddress
    lltv: UnitRatio
    lltv_raw: RawUint

    total_supply_assets: NonNegativeDecimal | None = None
    total_supply_assets_raw: RawUint | None = None
    total_borrow_assets: NonNegativeDecimal | None = None
    total_borrow_assets_raw: RawUint | None = None
    total_supply_shares_raw: RawUint | None = None
    total_borrow_shares_raw: RawUint | None = None
    utilization: UnitRatio | None = None
    supply_apy: Ratio | None = None
    borrow_apy: Ratio | None = None
    fee: UnitRatio | None = None
    last_update: UtcDatetime | None = None

    oracle_price: OraclePriceDTO | None = None
    collateral_price: MarketPriceDTO | None = None
    loan_price: MarketPriceDTO | None = None

    @model_validator(mode="after")
    def _consistency(self) -> "MorphoMarketFields":
        if self.lltv != scale_raw(self.lltv_raw, WAD_DECIMALS):
            raise ValueError("lltv inconsistent with lltv_raw / 1e18")
        dec = self.loan_token.decimals
        for name in ("total_supply_assets", "total_borrow_assets"):
            value, raw = getattr(self, name), getattr(self, f"{name}_raw")
            if value is not None and raw is not None and dec is not None:
                if value != scale_raw(raw, dec):
                    raise ValueError(f"{name} inconsistent with {name}_raw")
        if self.oracle_price is not None:
            op = self.oracle_price
            if op.oracle_address != self.oracle_address:
                raise ValueError("oracle_price.oracle_address != market oracle")
            if not _same(op.base_asset, self.collateral_token) or not _same(
                op.quote_asset, self.loan_token
            ):
                raise ValueError("oracle_price must price collateral in loan token")
        if self.collateral_price is not None and not _same(
            self.collateral_price.base_asset, self.collateral_token
        ):
            raise ValueError("collateral_price.base_asset != collateral_token")
        if self.loan_price is not None and not _same(self.loan_price.base_asset, self.loan_token):
            raise ValueError("loan_price.base_asset != loan_token")
        return self


class PendleMarketFields(ContractModel):
    """Pendle V2 market: PT/YT/SY triple, expiry and API metrics."""

    METRIC_FIELDS: ClassVar[tuple[str, ...]] = (
        "underlying_asset",
        "is_expired",
        "implied_apy",
        "underlying_apy",
        "pt_price",
        "liquidity_usd",
    )

    market_address: EvmAddress
    pt: TokenRef
    yt: TokenRef
    sy: TokenRef
    expiry: UtcDatetime

    underlying_asset: TokenRef | None = None
    is_expired: StrictBool | None = None
    implied_apy: Ratio | None = None
    underlying_apy: Ratio | None = None
    pt_price: MarketPriceDTO | None = None
    liquidity_usd: NonNegativeDecimal | None = None
    total_pt: NonNegativeDecimal | None = None
    total_sy: NonNegativeDecimal | None = None

    @model_validator(mode="after")
    def _consistency(self) -> "PendleMarketFields":
        keys = {self.pt.key, self.yt.key, self.sy.key, (self.pt.chain_id, self.market_address)}
        if len(keys) != 4:
            raise ValueError("market, PT, YT and SY addresses must be distinct")
        if self.pt_price is not None and not _same(self.pt_price.base_asset, self.pt):
            raise ValueError("pt_price.base_asset != pt")
        return self


class MarketDTO(VersionedModel):
    """Normalised market. Exactly one protocol block is set, matching `protocol`.

    market_id: bytes32 id for Morpho Blue, market contract address for Pendle.
    """

    protocol: Protocol
    market_id: str
    status: DataStatus
    as_of: UtcDatetime
    block_number: BlockNumber | None = None
    morpho: MorphoMarketFields | None = None
    pendle: PendleMarketFields | None = None
    verification: tuple[VerificationCheckDTO, ...] = ()
    source_ids: tuple[SourceId, ...] = Field(min_length=1)
    evidence_ids: tuple[EvidenceId, ...] = Field(min_length=1)

    @property
    def market_key(self) -> str:
        """`morpho:<chain>:<bytes32 id>` or `pendle:<chain>:<market address>`."""
        prefix = "morpho" if self.protocol == Protocol.MORPHO_BLUE else "pendle"
        return f"{prefix}:{self.chain_id}:{self.market_id}"

    @model_validator(mode="before")
    @classmethod
    def _normalize_market_id(cls, data):
        if isinstance(data, dict) and isinstance(data.get("market_id"), str):
            mid = data["market_id"].strip()
            data = {**data, "market_id": mid.lower() if mid.startswith(("0x", "0X")) else mid}
        return data

    @model_validator(mode="after")
    def _consistency(self) -> "MarketDTO":
        if self.protocol == Protocol.MORPHO_BLUE:
            if self.morpho is None or self.pendle is not None:
                raise ValueError("MORPHO_BLUE market requires `morpho` block only")
            normalize_bytes32(self.market_id)
            block, label = self.morpho, "morpho."
        else:
            if self.pendle is None or self.morpho is not None:
                raise ValueError("PENDLE_V2 market requires `pendle` block only")
            if normalize_address(self.market_id) != self.pendle.market_address:
                raise ValueError("market_id must equal pendle.market_address")
            block, label = self.pendle, "pendle."

        for token in self._tokens():
            if token.chain_id != self.chain_id:
                raise ValueError("token chain_id differs from market chain_id")

        enforce_status(
            self.status,
            missing_critical=[],
            missing_metrics=missing_fields(block, block.METRIC_FIELDS, label),
            checks=self.verification,
            label=self.market_key,
        )
        if self.status in (DataStatus.OK, DataStatus.PARTIAL) and self.block_number is None:
            raise ValueError("verified market requires block_number")
        return self

    def _tokens(self) -> list[TokenRef]:
        if self.morpho is not None:
            return [self.morpho.loan_token, self.morpho.collateral_token]
        assert self.pendle is not None
        toks = [self.pendle.pt, self.pendle.yt, self.pendle.sy]
        if self.pendle.underlying_asset is not None:
            toks.append(self.pendle.underlying_asset)
        return toks