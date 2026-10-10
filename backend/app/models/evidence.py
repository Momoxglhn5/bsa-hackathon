"""Data contract v1.0 — shared primitives, provenance and evidence.

All other model modules import their primitives from here so that address
normalisation, Decimal handling, status semantics and provenance are defined
exactly once.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from enum import Enum
from typing import Annotated, Any, Iterable, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictInt,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "data-contract-v1.0"
SchemaVersion = Literal["data-contract-v1.0"]

ETHEREUM_MAINNET_CHAIN_ID = 1
SUPPORTED_CHAIN_IDS = frozenset({ETHEREUM_MAINNET_CHAIN_ID})

UINT256_MAX = 2**256 - 1
ZERO_ADDRESS = "0x" + "0" * 40

_ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")
_BYTES32_RE = re.compile(r"^0x[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HIGH_PRECISION = 120


# ---------------------------------------------------------------------------
# Normalisers / parsers
# ---------------------------------------------------------------------------

def normalize_address(value: Any) -> str:
    """Return the canonical form of an EVM address: lowercase, 0x-prefixed."""
    if not isinstance(value, str):
        raise ValueError("EVM address must be a string")
    v = value.strip().lower()
    if not _ADDRESS_RE.match(v):
        raise ValueError(f"invalid EVM address: {value!r}")
    return v


def normalize_bytes32(value: Any) -> str:
    """Return the canonical form of a bytes32 hex value (e.g. a Morpho market id)."""
    if not isinstance(value, str):
        raise ValueError("bytes32 value must be a string")
    v = value.strip().lower()
    if not _BYTES32_RE.match(v):
        raise ValueError(f"invalid bytes32 hex: {value!r}")
    return v


def to_decimal(value: Any) -> Decimal:
    """Parse a precise numeric value. Floats and bools are rejected."""
    if isinstance(value, (bool, float)):
        raise ValueError(
            "float/bool not accepted for precise values; pass str, int or Decimal"
        )
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            raise ValueError("empty string is not a number; use None for missing")
        try:
            d = Decimal(s)
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal number: {value!r}") from exc
    else:
        raise ValueError(f"unsupported type for decimal: {type(value).__name__}")
    if not d.is_finite():
        raise ValueError("NaN/Infinity not allowed")
    return d


def to_raw_uint(value: Any) -> int:
    """Parse a raw on-chain uint256 (int, decimal string or 0x-hex string)."""
    if isinstance(value, bool):
        raise ValueError("bool is not a uint256")
    if isinstance(value, int):
        n = value
    elif isinstance(value, str):
        s = value.strip()
        if s[:2].lower() == "0x":
            try:
                n = int(s, 16)
            except ValueError as exc:
                raise ValueError(f"invalid hex uint: {value!r}") from exc
        elif s.isdigit():
            n = int(s)
        else:
            raise ValueError(f"invalid uint string: {value!r}")
    else:
        raise ValueError(f"unsupported type for uint256: {type(value).__name__}")
    if not 0 <= n <= UINT256_MAX:
        raise ValueError("value outside uint256 range")
    return n


def decimal_to_str(d: Decimal) -> str:
    """Plain (non-scientific) string representation of a Decimal."""
    return format(d, "f")


def scale_raw(raw: int, exponent: int) -> Decimal:
    """Exact conversion raw / 10**exponent (no rounding)."""
    with localcontext() as ctx:
        ctx.prec = _HIGH_PRECISION
        return Decimal(raw).scaleb(-exponent)


def _non_negative(d: Decimal) -> Decimal:
    if d < 0:
        raise ValueError("must be >= 0")
    return d


def _positive(d: Decimal) -> Decimal:
    if d <= 0:
        raise ValueError("must be > 0")
    return d


def _unit_interval(d: Decimal) -> Decimal:
    if not Decimal(0) <= d <= Decimal(1):
        raise ValueError("ratio must be within [0, 1]")
    return d


def _to_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Annotated field types
# ---------------------------------------------------------------------------

EvmAddress = Annotated[str, BeforeValidator(normalize_address)]
Bytes32 = Annotated[str, BeforeValidator(normalize_bytes32)]

PreciseDecimal = Annotated[
    Decimal,
    BeforeValidator(to_decimal),
    PlainSerializer(decimal_to_str, return_type=str, when_used="json"),
]
NonNegativeDecimal = Annotated[PreciseDecimal, AfterValidator(_non_negative)]
PositiveDecimal = Annotated[PreciseDecimal, AfterValidator(_positive)]

Ratio = PreciseDecimal
"""Rate expressed as a ratio: 0.05 == 5 %. Never a percentage."""
UnitRatio = Annotated[PreciseDecimal, AfterValidator(_unit_interval)]
"""Ratio constrained to [0, 1] (utilisation, LLTV, fee)."""

RawUint = Annotated[
    int,
    BeforeValidator(to_raw_uint),
    PlainSerializer(str, return_type=str, when_used="json"),
]
"""Raw uint256 exactly as returned on-chain; serialised as a string in JSON."""

BlockNumber = Annotated[StrictInt, Field(ge=0)]
TokenDecimals = Annotated[StrictInt, Field(ge=0, le=255)]
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]

EvidenceId = Annotated[str, Field(pattern=r"^ev_[0-9a-f]{16,64}$")]
SourceId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.:\-]*$", max_length=128)]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DataMode(str, Enum):
    REAL = "REAL"
    DEMO = "DEMO"


class DataStatus(str, Enum):
    OK = "OK"                  # all expected fields present and verified
    PARTIAL = "PARTIAL"        # essentials verified, some metrics missing (None)
    MISSING = "MISSING"        # essential data absent
    STALE = "STALE"            # data present but older than accepted freshness
    UNVERIFIED = "UNVERIFIED"  # API data not (yet) confirmed on-chain
    MISMATCH = "MISMATCH"      # API and on-chain values disagree
    ERROR = "ERROR"            # retrieval/parsing failed


class SourceKind(str, Enum):
    MORPHO_API = "MORPHO_API"
    PENDLE_API = "PENDLE_API"
    RPC = "RPC"
    FIXTURE = "FIXTURE"
    USER_INPUT = "USER_INPUT"
    DERIVED = "DERIVED"


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    MISMATCH = "MISMATCH"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# Base models
# ---------------------------------------------------------------------------

class ContractModel(BaseModel):
    """Immutable base: unknown fields rejected, defaults validated."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)


class ChainScopedModel(ContractModel):
    chain_id: StrictInt

    @field_validator("chain_id")
    @classmethod
    def _supported_chain(cls, v: int) -> int:
        if v not in SUPPORTED_CHAIN_IDS:
            raise ValueError(
                f"chain_id {v} not supported (MVP: {sorted(SUPPORTED_CHAIN_IDS)})"
            )
        return v


class VersionedModel(ChainScopedModel):
    """Top-level DTO: carries schema version and data mode."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    data_mode: DataMode


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

USABLE_STATUSES = frozenset({DataStatus.OK, DataStatus.PARTIAL})


def missing_fields(obj: BaseModel, names: Iterable[str], prefix: str = "") -> list[str]:
    return [f"{prefix}{n}" for n in names if getattr(obj, n) is None]


def enforce_status(
    status: DataStatus,
    *,
    missing_critical: list[str],
    missing_metrics: list[str],
    checks: tuple["VerificationCheckDTO", ...] | None = None,
    label: str,
) -> None:
    """Missing data is never OK: raise if `status` overstates data quality."""
    if missing_critical and status in USABLE_STATUSES:
        raise ValueError(f"{label}: status {status.value} with missing critical {missing_critical}")
    if missing_metrics and status == DataStatus.OK:
        raise ValueError(f"{label}: status OK with missing metrics {missing_metrics}; use PARTIAL")
    if checks is not None:
        statuses = {c.status for c in checks}
        if VerificationStatus.MISMATCH in statuses and status not in (
            DataStatus.MISMATCH,
            DataStatus.ERROR,
        ):
            raise ValueError(f"{label}: verification mismatch requires status MISMATCH or ERROR")
        if status in USABLE_STATUSES and (
            not checks or statuses != {VerificationStatus.VERIFIED}
        ):
            raise ValueError(f"{label}: status {status.value} requires all checks VERIFIED")


# ---------------------------------------------------------------------------
# Evidence / provenance
# ---------------------------------------------------------------------------

def hash_payload(payload: Any) -> str:
    """Deterministic sha256 of a raw API/RPC payload."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def make_evidence_id(
    source_id: str,
    method: str,
    request_params: dict[str, str] | None = None,
    block_number: int | None = None,
    fetched_at: datetime | None = None,
) -> str:
    """Deterministic evidence id from the request identity.

    RPC reads are identified by block; API reads by fetch time.
    """
    anchor = str(block_number) if block_number is not None else (
        fetched_at.astimezone(timezone.utc).isoformat() if fetched_at else ""
    )
    blob = json.dumps(
        [source_id, method, sorted((request_params or {}).items()), anchor],
        separators=(",", ":"),
    )
    return "ev_" + hashlib.sha256(blob.encode()).hexdigest()[:24]


class EvidenceDTO(VersionedModel):
    """One retrieval from one source. Other DTOs reference it by `evidence_id`."""

    evidence_id: EvidenceId
    source_kind: SourceKind
    source_id: SourceId
    method: str = Field(min_length=1, max_length=256)
    target_address: EvmAddress | None = None
    request_params: dict[str, str] = Field(default_factory=dict)
    block_number: BlockNumber | None = None
    block_hash: Bytes32 | None = None
    fetched_at: UtcDatetime
    payload_sha256: str | None = None
    status: DataStatus
    error_message: str | None = None

    @field_validator("payload_sha256")
    @classmethod
    def _sha(cls, v: str | None) -> str | None:
        if v is not None and not _SHA256_RE.match(v):
            raise ValueError("payload_sha256 must be 64 lowercase hex chars")
        return v

    @model_validator(mode="after")
    def _consistency(self) -> "EvidenceDTO":
        if self.data_mode == DataMode.DEMO and self.source_kind not in (
            SourceKind.FIXTURE,
            SourceKind.USER_INPUT,
            SourceKind.DERIVED,
        ):
            raise ValueError("DEMO evidence must come from FIXTURE, USER_INPUT or DERIVED")
        if self.data_mode == DataMode.REAL and self.source_kind == SourceKind.FIXTURE:
            raise ValueError("REAL evidence cannot come from FIXTURE")
        if self.source_kind == SourceKind.RPC and self.block_number is None:
            raise ValueError("RPC evidence requires block_number")
        if self.status == DataStatus.ERROR and not self.error_message:
            raise ValueError("ERROR evidence requires error_message")
        if self.status != DataStatus.ERROR and self.error_message:
            raise ValueError("error_message only allowed with status ERROR")
        return self


class VerificationCheckDTO(ContractModel):
    """API-claimed value vs value read on-chain for one essential relationship.

    Values are canonical strings (normalised address, bytes32, integer string).
    A mismatch keeps both values; nothing is silently resolved.
    """

    check: str = Field(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)*$", max_length=128)
    claimed_value: str | None = None
    claimed_source_id: SourceId | None = None
    onchain_value: str | None = None
    status: VerificationStatus
    evidence_ids: tuple[EvidenceId, ...] = ()

    @model_validator(mode="after")
    def _consistency(self) -> "VerificationCheckDTO":
        both = self.claimed_value is not None and self.onchain_value is not None
        if self.status == VerificationStatus.VERIFIED:
            if self.onchain_value is None:
                raise ValueError(f"{self.check}: VERIFIED requires onchain_value")
            if self.claimed_value is not None and self.claimed_value != self.onchain_value:
                raise ValueError(f"{self.check}: VERIFIED but values differ")
            if not self.evidence_ids:
                raise ValueError(f"{self.check}: VERIFIED requires evidence_ids")
        if self.status == VerificationStatus.MISMATCH:
            if not both or self.claimed_value == self.onchain_value:
                raise ValueError(f"{self.check}: MISMATCH requires two differing values")
        if self.claimed_value is not None and self.claimed_source_id is None:
            raise ValueError(f"{self.check}: claimed_value requires claimed_source_id")
        return self