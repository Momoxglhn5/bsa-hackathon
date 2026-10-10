"""Map raw Morpho / Pendle API payloads and RPC reads to the v1 data contracts.

Rules applied here:
  * keys: `asset:1:<addr>`, `morpho:1:<bytes32 id>`, `pendle:1:<market addr>` (lowercase);
    symbols are labels only and never used as keys;
  * a Morpho market id is a bytes32 identifier, never an address;
  * numbers: Decimal (floats rejected — parse JSON with parse_float=Decimal), raw on-chain
    integers kept alongside scaled values; LLTV and APYs are ratios (0.05 == 5 %);
  * missing / null / not-fetched numbers become None with a recorded reason, never 0;
  * API values are cross-checked against RPC reads; on disagreement the on-chain value is
    used in the DTO, both values are kept in the VerificationCheckDTO and status = MISMATCH;
  * no health factor, no risk metric, no recommendation.

Source-field mapping (verified against live responses on 2026-10-10):
  Morpho REST /v1/blue/markets item: market_id, chain_id, loan_token, collateral_token,
    oracle_address, irm_address, lltv_wad (raw WAD string). No symbols, decimals or state.
  Pendle /v2/markets/all item: chainId, address, expiry (ISO), pt / yt / sy / underlyingAsset
    ("<chainId>-<address>"), details.{impliedApy, underlyingApy, liquidity, totalPt, totalSy}.
  Pendle /v3/.../historical-data: results[].{timestamp, impliedApy, underlyingApy, ...}.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from enum import Enum
from typing import Any, Iterable, Mapping

from app.models.evidence import (
    ETHEREUM_MAINNET_CHAIN_ID,
    DataMode,
    DataStatus,
    EvidenceDTO,
    RelationDTO,
    RelationStatus,
    RelationType,
    VerificationCheckDTO,
    VerificationStatus,
    make_relation,
    missing_fields,
    normalize_address,
    normalize_bytes32,
    scale_raw,
    to_decimal,
    to_raw_uint,
)
from app.models.market import (
    MORPHO_ORACLE_PRICE_SCALE,
    WAD_DECIMALS,
    MarketDTO,
    MarketPriceDTO,
    MorphoMarketFields,
    OraclePriceDTO,
    PendleMarketFields,
    PricingMethod,
    Protocol,
    QuoteUnit,
    TokenRef,
)
from app.models.timeseries import (
    SeriesUnit,
    SubjectKind,
    TimeseriesDTO,
    TimeseriesMetric,
    TimeseriesPointDTO,
)

CHAIN_ID = ETHEREUM_MAINNET_CHAIN_ID

# Morpho Blue singleton on Ethereum mainnet (docs.morpho.org, contract addresses).
# Not returned by the REST markets endpoint; RPC reads are made against it.
MORPHO_BLUE_ADDRESS = "0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb"

# `details.liquidity` carries no unit in the payload; Pendle displays it in USD.
PENDLE_LIQUIDITY_UNIT_ASSUMED = "USD"

_PENDLE_ID_RE = re.compile(r"^(\d+)-(0x[0-9a-fA-F]{40})$")
_TIME_FRAME_SECONDS = {"hour": 3600, "day": 86400, "week": 604800}


class NormalizationError(ValueError):
    """Raw payload cannot be mapped to the contract (critical field absent or invalid)."""


class MissingReason(str, Enum):
    ABSENT_IN_SOURCE = "ABSENT_IN_SOURCE"   # key not present in the payload
    NULL_IN_SOURCE = "NULL_IN_SOURCE"       # key present, value null
    NOT_FETCHED = "NOT_FETCHED"             # source not queried (e.g. no RPC read yet)
    DECIMALS_UNKNOWN = "DECIMALS_UNKNOWN"   # raw value present but cannot be scaled
    UNDEFINED = "UNDEFINED"                 # mathematically undefined (e.g. 0 supply)
    NO_VERIFIED_SOURCE = "NO_VERIFIED_SOURCE"


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def asset_id(address: str, chain_id: int = CHAIN_ID) -> str:
    return f"asset:{chain_id}:{normalize_address(address)}"


def contract_id(address: str, chain_id: int = CHAIN_ID) -> str:
    """Node id for a non-token contract (oracle, IRM)."""
    return f"contract:{chain_id}:{normalize_address(address)}"


def morpho_market_key(market_id: str, chain_id: int = CHAIN_ID) -> str:
    return f"morpho:{chain_id}:{normalize_bytes32(market_id)}"


def pendle_market_key(market_address: str, chain_id: int = CHAIN_ID) -> str:
    return f"pendle:{chain_id}:{normalize_address(market_address)}"


def parse_pendle_id(value: Any) -> tuple[int, str]:
    """"1-0xAbC…" -> (1, "0xabc…")."""
    if not isinstance(value, str):
        raise NormalizationError(f"Pendle id must be a string, got {type(value).__name__}")
    match = _PENDLE_ID_RE.match(value.strip())
    if not match:
        raise NormalizationError(f"invalid Pendle id: {value!r}")
    return int(match.group(1)), normalize_address(match.group(2))


# ---------------------------------------------------------------------------
# Inputs: provenance and RPC reads (produced by the adapters / RPC layer)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceRef:
    source_id: str
    evidence_id: str
    observed_at: datetime

    @classmethod
    def from_response(cls, raw: Any) -> "SourceRef":
        """Build from an adapter RawResponse (source_id, evidence_id, retrieved_at)."""
        return cls(raw.source_id, raw.evidence_id, raw.retrieved_at)


@dataclass(frozen=True)
class TokenMeta:
    """ERC20 metadata read via RPC. symbol is a label only."""
    address: str
    decimals: int | None
    symbol: str | None
    ref: SourceRef
    name: str | None = None


@dataclass(frozen=True)
class OnchainMarketParams:
    """Morpho.idToMarketParams(id)."""
    market_id: str
    loan_token: str
    collateral_token: str
    oracle: str
    irm: str
    lltv_raw: int
    block_number: int
    ref: SourceRef


@dataclass(frozen=True)
class OnchainMarketState:
    """Morpho.market(id)."""
    market_id: str
    total_supply_assets_raw: int
    total_supply_shares_raw: int
    total_borrow_assets_raw: int
    total_borrow_shares_raw: int
    last_update: int
    fee_raw: int
    block_number: int
    ref: SourceRef


@dataclass(frozen=True)
class OracleRead:
    """IOracle(oracle).price() — collateral quoted in loan token, scaled 1e36 (+ decimals)."""
    oracle_address: str
    price_raw: int
    block_number: int
    ref: SourceRef


@dataclass(frozen=True)
class MorphoApys:
    """APYs from the Morpho API, as ratios. None when absent."""
    supply_apy: Decimal | None
    borrow_apy: Decimal | None
    ref: SourceRef


@dataclass(frozen=True)
class OnchainPendleTokens:
    """PendleMarket.readTokens() + PT.expiry()."""
    market_address: str
    sy: str
    pt: str
    yt: str
    expiry_ts: int | None
    block_number: int
    ref: SourceRef


class SyAssetType(int, Enum):
    """Pendle IStandardizedYield.AssetType."""
    TOKEN = 0
    LIQUIDITY = 1


@dataclass(frozen=True)
class OnchainSyAssetInfo:
    """SY.yieldToken() + SY.assetInfo() (the SY's accounting asset).

    Neither read proves 1:1 economic backing or redeemability of the SY.
    """
    sy_address: str
    yield_token: str
    asset_type: SyAssetType
    asset_address: str
    asset_decimals: int
    block_number: int
    ref: SourceRef              # assetInfo() evidence
    yield_token_ref: SourceRef  # yieldToken() evidence


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NormalizedMarket:
    dto: MarketDTO
    key: str
    missing: dict[str, MissingReason] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedSeries:
    dto: TimeseriesDTO
    missing_points: int
    dropped_duplicates: int


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------

def _get(container: Any, path: str, missing: dict[str, MissingReason], label: str) -> Any:
    obj = container
    for part in path.split("."):
        if not isinstance(obj, Mapping) or part not in obj:
            missing[label] = MissingReason.ABSENT_IN_SOURCE
            return None
        obj = obj[part]
    if obj is None:
        missing[label] = MissingReason.NULL_IN_SOURCE
    return obj


def _decimal(container: Any, path: str, missing: dict[str, MissingReason], label: str) -> Decimal | None:
    value = _get(container, path, missing, label)
    if value is None:
        return None
    try:
        return to_decimal(value)
    except ValueError as exc:
        raise NormalizationError(f"{label}: {exc}") from exc


def _required(container: Any, path: str, label: str) -> Any:
    tmp: dict[str, MissingReason] = {}
    value = _get(container, path, tmp, label)
    if value is None:
        raise NormalizationError(f"critical field {label!r} ({path}) {tmp.get(label, '').lower()}")
    return value


def parse_iso_utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise NormalizationError(f"timestamp must be an ISO string, got {value!r}")
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise NormalizationError(f"invalid ISO timestamp {value!r}") from exc
    if dt.tzinfo is None:
        raise NormalizationError(f"timestamp without timezone: {value!r}")
    return dt.astimezone(timezone.utc)


def from_unix(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def token_ref(address: str, tokens: Mapping[str, TokenMeta] | None = None) -> TokenRef:
    """TokenRef keyed by address; symbol/decimals only from RPC metadata when available."""
    addr = normalize_address(address)
    meta = (tokens or {}).get(addr)
    return TokenRef(
        chain_id=CHAIN_ID,
        address=addr,
        symbol=meta.symbol if meta else None,
        name=meta.name if meta else None,
        decimals=meta.decimals if meta else None,
    )


def _ratio(numerator_raw: int, denominator_raw: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 50
        return Decimal(numerator_raw) / Decimal(denominator_raw)


def _check(
    name: str,
    claimed: str | None,
    claimed_source_id: str | None,
    onchain: str | None,
    evidence_ids: tuple[str, ...],
) -> VerificationCheckDTO:
    if onchain is None:
        status = VerificationStatus.UNVERIFIED
        evidence_ids = ()
    elif claimed is None or claimed == onchain:
        status = VerificationStatus.VERIFIED
    else:
        status = VerificationStatus.MISMATCH
    return VerificationCheckDTO(
        check=name,
        claimed_value=claimed,
        claimed_source_id=claimed_source_id if claimed is not None else None,
        onchain_value=onchain,
        status=status,
        evidence_ids=evidence_ids,
    )


def derive_status(
    missing_metrics: list[str],
    checks: tuple[VerificationCheckDTO, ...],
    block_number: int | None,
) -> DataStatus:
    """Most conservative status consistent with the data actually present."""
    statuses = {c.status for c in checks}
    if VerificationStatus.MISMATCH in statuses:
        return DataStatus.MISMATCH
    if VerificationStatus.ERROR in statuses:
        return DataStatus.ERROR
    if not checks or statuses != {VerificationStatus.VERIFIED} or block_number is None:
        return DataStatus.UNVERIFIED
    return DataStatus.PARTIAL if missing_metrics else DataStatus.OK


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

def build_market_price(
    asset: TokenRef,
    value: Any,
    *,
    quote_unit: QuoteUnit,
    pricing_method: PricingMethod,
    ref: SourceRef,
    quote_asset: TokenRef | None = None,
    block_number: int | None = None,
) -> MarketPriceDTO | None:
    """API price -> MarketPriceDTO. Returns None (never 0) when value is missing."""
    if value is None:
        return None
    return MarketPriceDTO(
        chain_id=CHAIN_ID,
        value=to_decimal(value),
        base_asset=asset,
        quote_unit=quote_unit,
        quote_asset=quote_asset,
        pricing_method=pricing_method,
        observed_at=ref.observed_at,
        block_number=block_number,
        source_id=ref.source_id,
        evidence_ids=(ref.evidence_id,),
        status=DataStatus.OK,
    )


def price_from_pendle_prices(
    prices: Mapping[str, Any],
    asset: TokenRef,
    *,
    quote_unit: QuoteUnit,
    ref: SourceRef,
    quote_asset: TokenRef | None = None,
) -> MarketPriceDTO | None:
    """Pick one asset from a Pendle /v1/prices/assets map ("1-0x…" -> value).

    The payload states no currency: the caller must pass `quote_unit` explicitly.
    """
    value = prices.get(f"{CHAIN_ID}-{asset.address}")
    return build_market_price(
        asset, value, quote_unit=quote_unit, pricing_method=PricingMethod.API_SPOT,
        ref=ref, quote_asset=quote_asset,
    )


def oracle_price(
    oracle: OracleRead, collateral: TokenRef, loan: TokenRef
) -> tuple[OraclePriceDTO | None, MissingReason | None]:
    if collateral.decimals is None or loan.decimals is None:
        return None, MissingReason.DECIMALS_UNKNOWN
    exponent = MORPHO_ORACLE_PRICE_SCALE + loan.decimals - collateral.decimals
    return OraclePriceDTO(
        chain_id=CHAIN_ID,
        value=scale_raw(oracle.price_raw, exponent),
        base_asset=collateral,
        quote_unit=QuoteUnit.TOKEN,
        quote_asset=loan,
        pricing_method=PricingMethod.MORPHO_ORACLE,
        observed_at=oracle.ref.observed_at,
        block_number=oracle.block_number,
        source_id=oracle.ref.source_id,
        evidence_ids=(oracle.ref.evidence_id,),
        status=DataStatus.OK,
        oracle_address=normalize_address(oracle.oracle_address),
        raw_value=oracle.price_raw,
        scale_exponent=exponent,
    ), None


# ---------------------------------------------------------------------------
# Morpho
# ---------------------------------------------------------------------------

def morpho_param_checks(
    item: Mapping[str, Any], api_ref: SourceRef, onchain: OnchainMarketParams | None
) -> tuple[VerificationCheckDTO, ...]:
    """REST market params vs Morpho.idToMarketParams(id)."""
    if onchain is None:
        return ()
    ev = (onchain.ref.evidence_id,)
    src = api_ref.source_id
    api_lltv = item.get("lltv_wad")
    return (
        _check("morpho.loan_token", _opt_addr(item.get("loan_token")), src,
               normalize_address(onchain.loan_token), ev),
        _check("morpho.collateral_token", _opt_addr(item.get("collateral_token")), src,
               normalize_address(onchain.collateral_token), ev),
        _check("morpho.oracle", _opt_addr(item.get("oracle_address")), src,
               normalize_address(onchain.oracle), ev),
        _check("morpho.irm", _opt_addr(item.get("irm_address")), src,
               normalize_address(onchain.irm), ev),
        _check("morpho.lltv", str(to_raw_uint(api_lltv)) if api_lltv is not None else None, src,
               str(onchain.lltv_raw), ev),
    )


def _opt_addr(value: Any) -> str | None:
    return normalize_address(value) if value is not None else None


def normalize_morpho_market(
    item: Mapping[str, Any],
    ref: SourceRef,
    *,
    data_mode: DataMode = DataMode.REAL,
    onchain_params: OnchainMarketParams | None = None,
    onchain_state: OnchainMarketState | None = None,
    oracle: OracleRead | None = None,
    apys: MorphoApys | None = None,
    tokens: Mapping[str, TokenMeta] | None = None,
    collateral_price: MarketPriceDTO | None = None,
    loan_price: MarketPriceDTO | None = None,
) -> NormalizedMarket:
    """Morpho REST market item (+ optional RPC reads / API APYs) -> MarketDTO."""
    if item.get("chain_id") != CHAIN_ID:
        raise NormalizationError(f"Morpho item chain_id={item.get('chain_id')!r}, expected {CHAIN_ID}")
    market_id = normalize_bytes32(_required(item, "market_id", "market_id"))
    key = morpho_market_key(market_id)
    missing: dict[str, MissingReason] = {}

    for read in (onchain_params, onchain_state):
        if read is not None and normalize_bytes32(read.market_id) != market_id:
            raise NormalizationError(f"{key}: RPC read is for another market ({read.market_id})")

    checks = morpho_param_checks(item, ref, onchain_params)

    # On-chain params are authoritative; API params are used only when no RPC read exists.
    if onchain_params is not None:
        loan_addr, coll_addr = onchain_params.loan_token, onchain_params.collateral_token
        oracle_addr, irm_addr, lltv_raw = onchain_params.oracle, onchain_params.irm, onchain_params.lltv_raw
    else:
        loan_addr = _required(item, "loan_token", "loan_token")
        coll_addr = _required(item, "collateral_token", "collateral_token")
        oracle_addr = _required(item, "oracle_address", "oracle_address")
        irm_addr = _required(item, "irm_address", "irm_address")
        lltv_raw = to_raw_uint(_required(item, "lltv_wad", "lltv_wad"))

    loan = token_ref(loan_addr, tokens)
    collateral = token_ref(coll_addr, tokens)

    state: dict[str, Any] = {}
    if onchain_state is not None:
        s = onchain_state
        state.update(
            total_supply_assets_raw=s.total_supply_assets_raw,
            total_borrow_assets_raw=s.total_borrow_assets_raw,
            total_supply_shares_raw=s.total_supply_shares_raw,
            total_borrow_shares_raw=s.total_borrow_shares_raw,
            fee=scale_raw(s.fee_raw, WAD_DECIMALS),
            last_update=from_unix(s.last_update) if s.last_update > 0 else None,
        )
        if loan.decimals is not None:
            state["total_supply_assets"] = scale_raw(s.total_supply_assets_raw, loan.decimals)
            state["total_borrow_assets"] = scale_raw(s.total_borrow_assets_raw, loan.decimals)
        else:
            missing["morpho.total_supply_assets"] = MissingReason.DECIMALS_UNKNOWN
            missing["morpho.total_borrow_assets"] = MissingReason.DECIMALS_UNKNOWN
        if s.total_supply_assets_raw > 0:
            state["utilization"] = _ratio(s.total_borrow_assets_raw, s.total_supply_assets_raw)
        else:
            missing["morpho.utilization"] = MissingReason.UNDEFINED
    else:
        for name in ("total_supply_assets", "total_borrow_assets", "utilization", "fee"):
            missing[f"morpho.{name}"] = MissingReason.NOT_FETCHED

    if apys is not None:
        state["supply_apy"] = apys.supply_apy
        state["borrow_apy"] = apys.borrow_apy
        for name in ("supply_apy", "borrow_apy"):
            if state[name] is None:
                missing[f"morpho.{name}"] = MissingReason.ABSENT_IN_SOURCE
    else:
        missing["morpho.supply_apy"] = MissingReason.NOT_FETCHED
        missing["morpho.borrow_apy"] = MissingReason.NOT_FETCHED

    oracle_dto = None
    if oracle is not None:
        if normalize_address(oracle.oracle_address) != normalize_address(oracle_addr):
            raise NormalizationError(f"{key}: oracle read for {oracle.oracle_address}, market uses {oracle_addr}")
        oracle_dto, reason = oracle_price(oracle, collateral, loan)
        if reason:
            missing["morpho.oracle_price"] = reason
    else:
        missing["morpho.oracle_price"] = MissingReason.NOT_FETCHED
    if collateral_price is None:
        missing.setdefault("morpho.collateral_price", MissingReason.NOT_FETCHED)
    if loan_price is None:
        missing.setdefault("morpho.loan_price", MissingReason.NOT_FETCHED)

    fields = MorphoMarketFields(
        morpho_address=MORPHO_BLUE_ADDRESS,
        loan_token=loan,
        collateral_token=collateral,
        oracle_address=oracle_addr,
        irm_address=irm_addr,
        lltv=scale_raw(lltv_raw, WAD_DECIMALS),
        lltv_raw=lltv_raw,
        oracle_price=oracle_dto,
        collateral_price=collateral_price,
        loan_price=loan_price,
        **state,
    )

    block_number = next(
        (r.block_number for r in (onchain_state, onchain_params, oracle) if r is not None), None
    )
    refs = [ref] + [r.ref for r in (onchain_params, onchain_state, oracle, apys) if r is not None]
    refs += [p_ref for p in (collateral_price, loan_price) if p is not None
             for p_ref in [SourceRef(p.source_id, p.evidence_ids[0], p.observed_at)]]
    as_of = max(r.observed_at for r in refs)

    dto = MarketDTO(
        chain_id=CHAIN_ID,
        data_mode=data_mode,
        protocol=Protocol.MORPHO_BLUE,
        market_id=market_id,
        status=derive_status(missing_fields(fields, fields.METRIC_FIELDS), checks, block_number),
        as_of=as_of,
        block_number=block_number,
        morpho=fields,
        verification=checks,
        source_ids=_dedupe(r.source_id for r in refs),
        evidence_ids=_dedupe(r.evidence_id for r in refs),
    )
    return NormalizedMarket(dto, key, missing)


def morpho_apys_from_graphql(raw_payload: Mapping[str, Any], ref: SourceRef) -> MorphoApys:
    """data.marketById.state.{supplyApy, borrowApy} -> MorphoApys (absent -> None).

    Field names come from the Morpho docs and are not yet validated against the live
    schema; the adapter raises on unknown fields, so a wrong name cannot pass silently.
    """
    missing: dict[str, MissingReason] = {}
    return MorphoApys(
        supply_apy=_decimal(raw_payload, "data.marketById.state.supplyApy", missing, "supply_apy"),
        borrow_apy=_decimal(raw_payload, "data.marketById.state.borrowApy", missing, "borrow_apy"),
        ref=ref,
    )


# ---------------------------------------------------------------------------
# Pendle
# ---------------------------------------------------------------------------

def _pendle_token(item: Mapping[str, Any], path: str, tokens: Mapping[str, TokenMeta] | None) -> TokenRef:
    chain_id, address = parse_pendle_id(_required(item, path, path))
    if chain_id != CHAIN_ID:
        raise NormalizationError(f"{path} on chain {chain_id}, expected {CHAIN_ID}")
    return token_ref(address, tokens)


def pendle_token_checks(
    item: Mapping[str, Any], api_ref: SourceRef, onchain: OnchainPendleTokens | None
) -> tuple[VerificationCheckDTO, ...]:
    """API pt/yt/sy/expiry vs PendleMarket.readTokens() and PT.expiry()."""
    if onchain is None:
        return ()
    ev = (onchain.ref.evidence_id,)
    src = api_ref.source_id

    def api_addr(key: str) -> str | None:
        return parse_pendle_id(item[key])[1] if item.get(key) is not None else None

    api_expiry = (
        str(int(parse_iso_utc(item["expiry"]).timestamp())) if item.get("expiry") is not None else None
    )
    return (
        _check("pendle.sy", api_addr("sy"), src, normalize_address(onchain.sy), ev),
        _check("pendle.pt", api_addr("pt"), src, normalize_address(onchain.pt), ev),
        _check("pendle.yt", api_addr("yt"), src, normalize_address(onchain.yt), ev),
        _check("pendle.expiry", api_expiry, src,
               str(onchain.expiry_ts) if onchain.expiry_ts is not None else None, ev),
    )


def pendle_sy_yield_token_check(
    item: Mapping[str, Any], api_ref: SourceRef, onchain: OnchainSyAssetInfo
) -> VerificationCheckDTO:
    """API underlyingAsset vs SY.yieldToken() (same layer; assetInfo() is a different one)."""
    api_asset = item.get("underlyingAsset")
    return _check(
        "pendle.sy_yield_token",
        parse_pendle_id(api_asset)[1] if api_asset is not None else None,
        api_ref.source_id,
        normalize_address(onchain.yield_token),
        (onchain.yield_token_ref.evidence_id,),
    )


BACKING_UNRESOLVED_NOTE = "assetInfo/yieldToken do not prove backing"


def build_strategy_relations(
    morpho_params: OnchainMarketParams,
    pendle_tokens: OnchainPendleTokens,
    sy_info: OnchainSyAssetInfo,
    evidence_by_id: Mapping[str, EvidenceDTO],
) -> list[RelationDTO]:
    """Edges of one Morpho x Pendle strategy from RPC reads taken at one pinned block."""
    blocks = {morpho_params.block_number, pendle_tokens.block_number, sy_info.block_number}
    if len(blocks) != 1:
        raise ValueError(f"strategy reads span several blocks: {sorted(blocks)}")
    if normalize_address(sy_info.sy_address) != normalize_address(pendle_tokens.sy):
        raise ValueError("sy_info is not for the SY returned by readTokens()")

    def ev(ref: SourceRef) -> list[EvidenceDTO]:
        if ref.evidence_id not in evidence_by_id:
            raise KeyError(f"evidence {ref.evidence_id} missing from evidence_by_id")
        return [evidence_by_id[ref.evidence_id]]

    def onchain(src: str, rel: RelationType, dst: str, ref: SourceRef) -> RelationDTO:
        return make_relation(
            source_node_id=src, target_node_id=dst, relation_type=rel,
            verification_status=RelationStatus.ONCHAIN_VERIFIED,
            evidence=ev(ref), observed_at=ref.observed_at,
        )

    morpho = morpho_market_key(morpho_params.market_id)
    pendle = pendle_market_key(pendle_tokens.market_address)
    sy = asset_id(sy_info.sy_address)
    yield_token = asset_id(sy_info.yield_token)
    accounting = asset_id(sy_info.asset_address)
    m_ref, p_ref = morpho_params.ref, pendle_tokens.ref
    relations = [
        onchain(morpho, RelationType.MARKET_LOAN_ASSET, asset_id(morpho_params.loan_token), m_ref),
        onchain(morpho, RelationType.MARKET_COLLATERAL_ASSET, asset_id(morpho_params.collateral_token), m_ref),
        onchain(morpho, RelationType.MARKET_ORACLE, contract_id(morpho_params.oracle), m_ref),
        onchain(morpho, RelationType.MARKET_IRM, contract_id(morpho_params.irm), m_ref),
        onchain(pendle, RelationType.PENDLE_MARKET_PT, asset_id(pendle_tokens.pt), p_ref),
        onchain(pendle, RelationType.PENDLE_MARKET_SY, sy, p_ref),
        onchain(pendle, RelationType.PENDLE_MARKET_YT, asset_id(pendle_tokens.yt), p_ref),
        onchain(sy, RelationType.SY_YIELD_TOKEN, yield_token, sy_info.yield_token_ref),
        onchain(sy, RelationType.SY_ACCOUNTING_ASSET, accounting, sy_info.ref),
    ]
    # If the yield token is the accounting asset itself, the open question is the SY's backing.
    relations.append(make_relation(
        source_node_id=yield_token if yield_token != accounting else sy,
        target_node_id=accounting,
        relation_type=RelationType.ECONOMIC_BACKING,
        verification_status=RelationStatus.UNRESOLVED,
        evidence=ev(sy_info.yield_token_ref) + ev(sy_info.ref),
        observed_at=sy_info.ref.observed_at,
        note=BACKING_UNRESOLVED_NOTE,
    ))
    return relations


# historicalState field -> (metric, is raw asset amount). Assets are BigInt raw units of
# the loan token; utilization and APYs are Float ratios (0.05 == 5 %).
_MORPHO_HISTORY_METRICS: dict[str, tuple[TimeseriesMetric, bool]] = {
    "supplyAssets": (TimeseriesMetric.TOTAL_SUPPLY_ASSETS, True),
    "borrowAssets": (TimeseriesMetric.TOTAL_BORROW_ASSETS, True),
    "utilization": (TimeseriesMetric.UTILIZATION, False),
    "supplyApy": (TimeseriesMetric.SUPPLY_APY, False),
    "borrowApy": (TimeseriesMetric.BORROW_APY, False),
}


def normalize_morpho_history(
    raw_payload: Mapping[str, Any],
    market_id: str,
    ref: SourceRef,
    *,
    time_frame: str,
    loan_token: TokenRef,
    data_mode: DataMode = DataMode.REAL,
) -> dict[TimeseriesMetric, NormalizedSeries]:
    """GraphQL marketById.historicalState -> one TimeseriesDTO per field present.

    Ratios stay ratios; asset amounts are scaled by the loan token decimals (unit TOKEN,
    quote_asset = loan token). Null y -> MISSING point, never 0. Fields absent from the
    payload (e.g. the other history query) produce no series.
    """
    if time_frame not in _TIME_FRAME_SECONDS:
        raise NormalizationError(f"invalid time_frame {time_frame!r}")
    market = normalize_bytes32(market_id)
    history = (((raw_payload or {}).get("data") or {}).get("marketById") or {}).get("historicalState")
    if not isinstance(history, Mapping):
        raise NormalizationError("payload without data.marketById.historicalState")

    out: dict[TimeseriesMetric, NormalizedSeries] = {}
    for source_field, (metric, is_assets) in _MORPHO_HISTORY_METRICS.items():
        rows = history.get(source_field)
        if rows is None:
            continue
        if not isinstance(rows, list):
            raise NormalizationError(f"historicalState.{source_field} is not a list")
        if is_assets and loan_token.decimals is None:
            raise NormalizationError(f"{source_field}: loan token decimals unknown, cannot scale")
        by_ts: dict[datetime, TimeseriesPointDTO] = {}
        dropped = 0
        for row in rows:
            x = _required(row, "x", f"{source_field}.x")
            if isinstance(x, bool) or not isinstance(x, (int, Decimal)) or x != int(x):
                raise NormalizationError(f"{source_field}.x must be integer unix seconds, got {x!r}")
            ts = from_unix(int(x))
            if ts in by_ts:
                dropped += 1
                continue
            y = row.get("y") if isinstance(row, Mapping) else None
            if y is None:
                value = None
            elif is_assets:
                value = scale_raw(to_raw_uint(y), loan_token.decimals)
            else:
                value = to_decimal(y)
            by_ts[ts] = TimeseriesPointDTO(
                timestamp=ts,
                value=value,
                status=DataStatus.OK if value is not None else DataStatus.MISSING,
                evidence_ids=(ref.evidence_id,),
            )
        points = tuple(by_ts[t] for t in sorted(by_ts))
        n_missing = sum(1 for p in points if p.value is None)
        status = (
            DataStatus.MISSING if not points or n_missing == len(points)
            else DataStatus.PARTIAL if n_missing
            else DataStatus.OK
        )
        dto = TimeseriesDTO(
            chain_id=CHAIN_ID,
            data_mode=data_mode,
            series_id=f"ts_morpho_{metric.value.lower()}_{time_frame}_{market[2:26]}",
            metric=metric,
            subject_kind=SubjectKind.MARKET,
            subject_id=market,
            unit=SeriesUnit.TOKEN if is_assets else SeriesUnit.RATIO,
            quote_asset=loan_token if is_assets else None,
            interval_seconds=_TIME_FRAME_SECONDS[time_frame],
            source_id=ref.source_id,
            status=status,
            points=points,
        )
        out[metric] = NormalizedSeries(dto, n_missing, dropped)
    return out


def normalize_pendle_market(
    item: Mapping[str, Any],
    ref: SourceRef,
    *,
    data_mode: DataMode = DataMode.REAL,
    onchain: OnchainPendleTokens | None = None,
    tokens: Mapping[str, TokenMeta] | None = None,
    pt_price: MarketPriceDTO | None = None,
) -> NormalizedMarket:
    """Pendle /v2/markets/all item (+ optional RPC readTokens) -> MarketDTO."""
    if item.get("chainId") != CHAIN_ID:
        raise NormalizationError(f"Pendle item chainId={item.get('chainId')!r}, expected {CHAIN_ID}")
    market_address = normalize_address(_required(item, "address", "address"))
    key = pendle_market_key(market_address)
    missing: dict[str, MissingReason] = {}

    if onchain is not None and normalize_address(onchain.market_address) != market_address:
        raise NormalizationError(f"{key}: RPC read is for another market ({onchain.market_address})")
    checks = pendle_token_checks(item, ref, onchain)

    if onchain is not None:
        pt, yt, sy = (token_ref(a, tokens) for a in (onchain.pt, onchain.yt, onchain.sy))
    else:
        pt, yt, sy = (_pendle_token(item, k, tokens) for k in ("pt", "yt", "sy"))

    if onchain is not None and onchain.expiry_ts is not None:
        expiry = from_unix(onchain.expiry_ts)
    else:
        expiry = parse_iso_utc(_required(item, "expiry", "expiry"))

    underlying = None
    if item.get("underlyingAsset") is not None:
        underlying = _pendle_token(item, "underlyingAsset", tokens)
    else:
        missing["pendle.underlying_asset"] = (
            MissingReason.NULL_IN_SOURCE if "underlyingAsset" in item else MissingReason.ABSENT_IN_SOURCE
        )

    as_of = max([ref.observed_at] + ([onchain.ref.observed_at] if onchain else []))
    if pt_price is None:
        missing["pendle.pt_price"] = MissingReason.NOT_FETCHED

    fields = PendleMarketFields(
        market_address=market_address,
        pt=pt,
        yt=yt,
        sy=sy,
        expiry=expiry,
        underlying_asset=underlying,
        is_expired=expiry <= as_of,
        implied_apy=_decimal(item, "details.impliedApy", missing, "pendle.implied_apy"),
        underlying_apy=_decimal(item, "details.underlyingApy", missing, "pendle.underlying_apy"),
        pt_price=pt_price,
        liquidity_usd=_decimal(item, "details.liquidity", missing, "pendle.liquidity_usd"),
        total_pt=_decimal(item, "details.totalPt", missing, "pendle.total_pt"),
        total_sy=_decimal(item, "details.totalSy", missing, "pendle.total_sy"),
    )

    refs = [ref] + ([onchain.ref] if onchain else [])
    if pt_price is not None:
        refs.append(SourceRef(pt_price.source_id, pt_price.evidence_ids[0], pt_price.observed_at))
    block_number = onchain.block_number if onchain else None

    dto = MarketDTO(
        chain_id=CHAIN_ID,
        data_mode=data_mode,
        protocol=Protocol.PENDLE_V2,
        market_id=market_address,
        status=derive_status(missing_fields(fields, fields.METRIC_FIELDS), checks, block_number),
        as_of=as_of,
        block_number=block_number,
        pendle=fields,
        verification=checks,
        source_ids=_dedupe(r.source_id for r in refs),
        evidence_ids=_dedupe(r.evidence_id for r in refs),
    )
    return NormalizedMarket(dto, key, missing)


_PENDLE_HISTORY_METRICS: dict[str, TimeseriesMetric] = {
    "impliedApy": TimeseriesMetric.PT_IMPLIED_APY,
    "underlyingApy": TimeseriesMetric.UNDERLYING_APY,
}


def normalize_pendle_history(
    raw_payload: Mapping[str, Any],
    market_address: str,
    ref: SourceRef,
    *,
    time_frame: str,
    data_mode: DataMode = DataMode.REAL,
) -> dict[TimeseriesMetric, NormalizedSeries]:
    """historical-data results -> one TimeseriesDTO per mapped metric (ratio APYs).

    Absent/null values become MISSING points (value None). Fields without a contract
    metric (maxApy, baseApy, tvl, ...) are not mapped.
    """
    if time_frame not in _TIME_FRAME_SECONDS:
        raise NormalizationError(f"invalid time_frame {time_frame!r}")
    results = raw_payload.get("results") if isinstance(raw_payload, Mapping) else None
    if not isinstance(results, list):
        raise NormalizationError("historical-data payload without list `results`")
    address = normalize_address(market_address)

    out: dict[TimeseriesMetric, NormalizedSeries] = {}
    for source_field, metric in _PENDLE_HISTORY_METRICS.items():
        by_ts: dict[datetime, TimeseriesPointDTO] = {}
        dropped = 0
        for row in results:
            ts = parse_iso_utc(_required(row, "timestamp", "timestamp"))
            if ts in by_ts:
                dropped += 1
                continue
            tmp: dict[str, MissingReason] = {}
            value = _decimal(row, source_field, tmp, source_field)
            by_ts[ts] = TimeseriesPointDTO(
                timestamp=ts,
                value=value,
                status=DataStatus.OK if value is not None else DataStatus.MISSING,
                evidence_ids=(ref.evidence_id,),
            )
        points = tuple(by_ts[t] for t in sorted(by_ts))
        n_missing = sum(1 for p in points if p.value is None)
        if not points or n_missing == len(points):
            status = DataStatus.MISSING
        elif n_missing:
            status = DataStatus.PARTIAL
        else:
            status = DataStatus.OK
        dto = TimeseriesDTO(
            chain_id=CHAIN_ID,
            data_mode=data_mode,
            series_id=f"ts_pendle_{metric.value.lower()}_{time_frame}_{address}",
            metric=metric,
            subject_kind=SubjectKind.MARKET,
            subject_id=address,
            unit=SeriesUnit.RATIO,
            interval_seconds=_TIME_FRAME_SECONDS[time_frame],
            source_id=ref.source_id,
            status=status,
            points=points,
        )
        out[metric] = NormalizedSeries(dto, n_missing, dropped)
    return out