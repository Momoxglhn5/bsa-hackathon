"""Ethereum mainnet RPC adapter — read-only eth_call at a pinned block.

Scope (data contract v1.0):
  * connect over HTTP, refuse anything that is not chain id 1;
  * pin one block per verification batch; every read uses block_identifier=pinned.number;
  * generic `call()` returns the decoded value, the raw return hex and an EvidenceDTO;
  * failures (revert, timeout, transport, decode) raise typed errors carrying an
    ERROR EvidenceDTO — never a default value;
  * no account, no private key, no signing, no transaction sending.

The RPC URL may embed an API key: it is never logged, stored or put in error
messages. Evidence keeps only the hostname (`rpc_host`).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv
from eth_utils import get_abi_output_types
from web3 import HTTPProvider, Web3
from web3.exceptions import ContractLogicError, Web3Exception

from app.ingestion.normalize import (
    MORPHO_BLUE_ADDRESS,
    OnchainMarketParams,
    OnchainMarketState,
    OnchainPendleTokens,
    OnchainSyAssetInfo,
    OracleRead,
    SourceRef,
    SyAssetType,
    _check,
)
from app.models.evidence import (
    ETHEREUM_MAINNET_CHAIN_ID,
    ZERO_ADDRESS,
    DataMode,
    DataStatus,
    EvidenceDTO,
    SourceKind,
    VerificationCheckDTO,
    VerificationStatus,
    hash_payload,
    make_evidence_id,
    normalize_address,
    normalize_bytes32,
)

logger = logging.getLogger(__name__)

SOURCE_ID = "ethereum_rpc"
CHAIN_ID = ETHEREUM_MAINNET_CHAIN_ID

RPC_URL_ENV = "ETHEREUM_RPC_URL"
TIMEOUT_ENV = "ETHEREUM_RPC_TIMEOUT_S"
PUBLIC_RPC_URL = "https://ethereum-rpc.publicnode.com"
DEFAULT_TIMEOUT_S = 15.0

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
DEFAULT_MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 4.0

PARSER_VERSION = "ethereum_rpc.v1"
_EXCERPT_MAX_CHARS = 256

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"  # backend/.env
_DEMO_MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "demo_strategy_manifest.json"

MORPHO_ID_TO_MARKET_PARAMS_ABI = {
    "type": "function",
    "name": "idToMarketParams",
    "stateMutability": "view",
    "inputs": [{"name": "id", "type": "bytes32"}],
    "outputs": [
        {"name": "loanToken", "type": "address"},
        {"name": "collateralToken", "type": "address"},
        {"name": "oracle", "type": "address"},
        {"name": "irm", "type": "address"},
        {"name": "lltv", "type": "uint256"},
    ],
}

_UINT128 = "uint128"
MORPHO_MARKET_ABI = {
    "type": "function",
    "name": "market",
    "stateMutability": "view",
    "inputs": [{"name": "id", "type": "bytes32"}],
    "outputs": [
        {"name": "totalSupplyAssets", "type": _UINT128},
        {"name": "totalSupplyShares", "type": _UINT128},
        {"name": "totalBorrowAssets", "type": _UINT128},
        {"name": "totalBorrowShares", "type": _UINT128},
        {"name": "lastUpdate", "type": _UINT128},
        {"name": "fee", "type": _UINT128},
    ],
}

ORACLE_PRICE_ABI = {
    "type": "function",
    "name": "price",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint256"}],
}

PENDLE_READ_TOKENS_ABI = {
    "type": "function",
    "name": "readTokens",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [
        {"name": "_SY", "type": "address"},
        {"name": "_PT", "type": "address"},
        {"name": "_YT", "type": "address"},
    ],
}

SY_ASSET_INFO_ABI = {
    "type": "function",
    "name": "assetInfo",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [
        {"name": "assetType", "type": "uint8"},
        {"name": "assetAddress", "type": "address"},
        {"name": "assetDecimals", "type": "uint8"},
    ],
}

SY_YIELD_TOKEN_ABI = {
    "type": "function",
    "name": "yieldToken",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "address"}],
}

SY_BACKING_NOTE = "economic backing beyond the SY: UNRESOLVED"

PT_EXPIRY_ABI = {
    "type": "function",
    "name": "expiry",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint256"}],
}

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class EthereumRPCError(Exception):
    """Base error. `evidence` is set when the failure happened at a pinned block."""

    def __init__(self, message: str, *, evidence: EvidenceDTO | None = None) -> None:
        super().__init__(message)
        self.evidence = evidence


class RpcUnreachableError(EthereumRPCError):
    """Endpoint could not be reached at connect time."""


class RpcChainMismatchError(EthereumRPCError):
    """eth_chainId is not 1 (Ethereum mainnet)."""


class RpcRevertError(EthereumRPCError):
    """eth_call reverted. Never retried."""


class RpcTimeoutError(EthereumRPCError):
    """Request timed out on every attempt."""


class RpcTransportError(EthereumRPCError):
    """HTTP/connection failure or JSON-RPC error response."""


class RpcDecodeError(EthereumRPCError):
    """Return data does not decode against the ABI (e.g. empty `0x` from a non-contract)."""


class MorphoMarketNotFoundError(EthereumRPCError):
    """idToMarketParams returned all zeros: no such market at the pinned block."""


class MorphoStateError(EthereumRPCError):
    """Morpho.market(id) is all zeros (market not created) or the oracle price is 0."""


class PendleMarketReadError(EthereumRPCError):
    """readTokens() returned a zero address: not a Pendle market (or not initialised)."""


class SyAssetInfoError(EthereumRPCError):
    """yieldToken()/assetInfo() returned a zero address or an unknown asset type."""


class BlockMismatchError(EthereumRPCError):
    """Two reads that must be compared were taken at different blocks."""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PinnedBlock:
    number: int
    hash: str  # normalised bytes32
    timestamp: datetime  # UTC


@dataclass(frozen=True)
class RpcCallResult:
    value: Any  # single output unwrapped; several outputs as a tuple
    raw_return: str  # 0x-prefixed hex, exactly as returned by eth_call
    evidence: EvidenceDTO
    source_ref: SourceRef


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _rpc_config() -> tuple[str, str]:
    """Return (url, mode). mode is "env" or "public fallback"."""
    if _ENV_FILE.is_file():
        load_dotenv(_ENV_FILE, override=False)
    url = os.environ.get(RPC_URL_ENV, "").strip()
    return (url, "env") if url else (PUBLIC_RPC_URL, "public fallback")


def _timeout_s() -> float:
    raw = os.environ.get(TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{TIMEOUT_ENV} must be a number of seconds, got {raw!r}") from None
    if value <= 0:
        raise ValueError(f"{TIMEOUT_ENV} must be > 0")
    return value


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class EthereumRpcClient:
    """Read-only mainnet client. Construction checks reachability and chain id."""

    def __init__(
        self,
        url: str | None = None,
        *,
        timeout_s: float | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        if url is None:
            url, self.mode = _rpc_config()
        else:
            self.mode = "explicit"
        self._url = url
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("RPC URL must be an http(s) URL with a host")
        self.rpc_host = parts.hostname
        self._url_path = parts.path if len(parts.path) > 1 else None
        self.max_attempts = max_attempts
        # Every evidence produced by call() (OK and ERROR), by evidence_id.
        self.evidence: dict[str, EvidenceDTO] = {}
        # web3's built-in retry is disabled: retries are bounded and explicit here.
        self._w3 = Web3(
            HTTPProvider(
                url,
                request_kwargs={"timeout": timeout_s or _timeout_s()},
                exception_retry_configuration=None,
            )
        )
        logger.info("Ethereum RPC: %s (host=%s)", self.mode, self.rpc_host)

        try:
            self.chain_id = self._with_retry(lambda: self._w3.eth.chain_id)
        except EthereumRPCError as exc:
            raise RpcUnreachableError(f"RPC unreachable (host={self.rpc_host}): {exc}") from None
        if self.chain_id != CHAIN_ID:
            raise RpcChainMismatchError(
                f"RPC host={self.rpc_host} reports chain id {self.chain_id}, expected {CHAIN_ID}"
            )

    # -- safety ------------------------------------------------------------

    def _redact(self, text: str) -> str:
        """Strip the URL (and its path, where keys usually live) from a message."""
        text = text.replace(self._url, f"<{self.rpc_host}>")
        if self._url_path:
            text = text.replace(self._url_path, "/<redacted>")
        return text

    def _describe(self, exc: BaseException) -> str:
        return self._redact(f"{type(exc).__name__}: {exc}")

    # -- retry -------------------------------------------------------------

    def _with_retry(self, fn: Callable[[], T]) -> T:
        """Retry only transient transport errors and HTTP 429/5xx; reverts pass through."""
        for attempt in range(1, self.max_attempts + 1):
            try:
                return fn()
            except requests.Timeout as exc:
                err: EthereumRPCError = RpcTimeoutError(self._describe(exc))
                retryable = True
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                err = RpcTransportError(f"HTTP {status}: {self._describe(exc)}")
                retryable = status in RETRYABLE_STATUS
            except requests.ConnectionError as exc:
                err = RpcTransportError(self._describe(exc))
                retryable = True
            if not retryable or attempt == self.max_attempts:
                raise type(err)(f"{err} (attempts={attempt})") from None
            delay = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** (attempt - 1))
            time.sleep(delay + random.uniform(0, BACKOFF_BASE_S))
        raise AssertionError("unreachable")

    # -- reads -------------------------------------------------------------

    def pin_block(self, block_identifier: str | int = "latest") -> PinnedBlock:
        """Fetch one block header; pass the result to every call of a verification batch."""
        try:
            block = self._with_retry(lambda: self._w3.eth.get_block(block_identifier))
        except Web3Exception as exc:
            raise RpcTransportError(self._describe(exc)) from None
        return PinnedBlock(
            number=int(block["number"]),
            hash=normalize_bytes32(Web3.to_hex(block["hash"])),
            timestamp=datetime.fromtimestamp(int(block["timestamp"]), tz=timezone.utc),
        )

    def call(
        self,
        address: str,
        abi_fragment: dict[str, Any],
        fn_name: str,
        args: list[Any] | tuple[Any, ...],
        pinned: PinnedBlock,
    ) -> RpcCallResult:
        """eth_call `fn_name(*args)` on `address` at `pinned.number` and decode the result."""
        target = normalize_address(address)
        output_types = get_abi_output_types(abi_fragment)
        signature = f"{fn_name}({','.join(i['type'] for i in abi_fragment.get('inputs', []))})"
        method = f"eth_call {signature}"
        request_params = {
            "rpc_host": self.rpc_host,
            "args": json.dumps(list(args), default=str, separators=(",", ":")),
        }
        evidence_id = make_evidence_id(SOURCE_ID, f"{method}@{target}", request_params, pinned.number)

        def evidence(status: DataStatus, *, payload_sha256: str | None = None,
                     error: str | None = None, excerpt: dict[str, str] | None = None) -> EvidenceDTO:
            ev = EvidenceDTO(
                chain_id=CHAIN_ID,
                data_mode=DataMode.REAL,
                evidence_id=evidence_id,
                source_kind=SourceKind.RPC,
                source_id=SOURCE_ID,
                method=method,
                target_address=target,
                request_params=request_params,
                block_number=pinned.number,
                block_hash=pinned.hash,
                fetched_at=datetime.now(timezone.utc),
                payload_sha256=payload_sha256,
                status=status,
                error_message=error,
                source_uri=self.rpc_host,
                decoded_excerpt=excerpt,
                parser_version=PARSER_VERSION,
            )
            self.evidence[evidence_id] = ev
            return ev

        def fail(cls: type[EthereumRPCError], message: str) -> EthereumRPCError:
            return cls(message, evidence=evidence(DataStatus.ERROR, error=message))

        contract = self._w3.eth.contract(address=Web3.to_checksum_address(target), abi=[abi_fragment])
        calldata = contract.encode_abi(fn_name, args=list(args))
        tx = {"to": contract.address, "data": calldata}
        try:
            raw = self._with_retry(lambda: self._w3.eth.call(tx, block_identifier=pinned.number))
        except ContractLogicError as exc:
            raise fail(RpcRevertError, self._describe(exc)) from None
        except EthereumRPCError as exc:
            raise fail(type(exc), str(exc)) from None
        except Web3Exception as exc:  # JSON-RPC error object from the node
            raise fail(RpcTransportError, self._describe(exc)) from None

        raw_hex = Web3.to_hex(raw)
        try:
            decoded = self._w3.codec.decode(output_types, raw)
        except Exception as exc:  # eth_abi raises several unrelated error types
            raise fail(RpcDecodeError, f"{self._describe(exc)} (raw={raw_hex[:132]})") from None

        names = [o.get("name") or f"out{i}" for i, o in enumerate(abi_fragment.get("outputs", []))]
        ev = evidence(
            DataStatus.OK,
            payload_sha256=hash_payload(raw_hex),
            excerpt={n: _excerpt_value(v) for n, v in zip(names, decoded)},
        )
        return RpcCallResult(
            value=decoded[0] if len(decoded) == 1 else tuple(decoded),
            raw_return=raw_hex,
            evidence=ev,
            source_ref=SourceRef(SOURCE_ID, evidence_id, pinned.timestamp),
        )


def _excerpt_value(value: Any) -> str:
    if isinstance(value, bytes):
        text = Web3.to_hex(value)
    elif isinstance(value, str) and Web3.is_address(value):
        text = normalize_address(value)
    else:
        text = str(value)
    return text[:_EXCERPT_MAX_CHARS]


# ---------------------------------------------------------------------------
# Morpho Blue
# ---------------------------------------------------------------------------

def read_morpho_market_params(
    client: EthereumRpcClient, market_id: str, pinned: PinnedBlock
) -> OnchainMarketParams:
    """Morpho.idToMarketParams(id) at the pinned block. Unknown market raises, never zeros."""
    market_id = normalize_bytes32(market_id)
    res = client.call(
        MORPHO_BLUE_ADDRESS, MORPHO_ID_TO_MARKET_PARAMS_ABI, "idToMarketParams", [market_id], pinned
    )
    loan, collateral, oracle, irm, lltv = res.value
    if normalize_address(loan) == ZERO_ADDRESS:
        raise _data_error(
            MorphoMarketNotFoundError, res,
            f"Morpho market {market_id} does not exist at block {pinned.number} (zero params)",
        )
    return OnchainMarketParams(
        market_id=market_id,
        loan_token=normalize_address(loan),
        collateral_token=normalize_address(collateral),
        oracle=normalize_address(oracle),
        irm=normalize_address(irm),
        lltv_raw=int(lltv),
        block_number=pinned.number,
        ref=res.source_ref,
    )


def read_morpho_market_state(
    client: EthereumRpcClient, market_id: str, pinned: PinnedBlock
) -> OnchainMarketState:
    """Morpho.market(id) at the pinned block. lastUpdate == 0 means the market does not exist."""
    market_id = normalize_bytes32(market_id)
    res = client.call(MORPHO_BLUE_ADDRESS, MORPHO_MARKET_ABI, "market", [market_id], pinned)
    supply_assets, supply_shares, borrow_assets, borrow_shares, last_update, fee = (int(v) for v in res.value)
    if last_update == 0:
        raise _data_error(
            MorphoStateError, res, f"Morpho market {market_id} not created at block {pinned.number}"
        )
    return OnchainMarketState(
        market_id=market_id,
        total_supply_assets_raw=supply_assets,
        total_supply_shares_raw=supply_shares,
        total_borrow_assets_raw=borrow_assets,
        total_borrow_shares_raw=borrow_shares,
        last_update=last_update,
        fee_raw=fee,
        block_number=pinned.number,
        ref=res.source_ref,
    )


def read_oracle_price(client: EthereumRpcClient, oracle: str, pinned: PinnedBlock) -> OracleRead:
    """IOracle(oracle).price() at the pinned block. A zero price is an error, never data."""
    oracle = normalize_address(oracle)
    res = client.call(oracle, ORACLE_PRICE_ABI, "price", [], pinned)
    if int(res.value) == 0:
        raise _data_error(MorphoStateError, res, f"oracle {oracle} returned price 0 at block {pinned.number}")
    return OracleRead(oracle_address=oracle, price_raw=int(res.value), block_number=pinned.number,
                      ref=res.source_ref)


def read_erc20_decimals(client: EthereumRpcClient, token: str, pinned: PinnedBlock) -> RpcCallResult:
    """ERC20 decimals() at the pinned block (value + evidence)."""
    return client.call(normalize_address(token), ERC20_DECIMALS_ABI, "decimals", [], pinned)


def _data_error(cls: type[EthereumRPCError], res: RpcCallResult, message: str) -> EthereumRPCError:
    """A call that succeeded but returned unusable data: same evidence, status ERROR."""
    return cls(
        message,
        evidence=res.evidence.model_copy(update={"status": DataStatus.ERROR, "error_message": message}),
    )


# ---------------------------------------------------------------------------
# Pendle
# ---------------------------------------------------------------------------

def read_pendle_market_tokens(
    client: EthereumRpcClient, market_address: str, pinned: PinnedBlock
) -> tuple[OnchainPendleTokens, EvidenceDTO]:
    """PendleMarket.readTokens() then PT.expiry() at the pinned block.

    Returns the tokens (ref = readTokens evidence) and the expiry() evidence.
    """
    market = normalize_address(market_address)
    res = client.call(market, PENDLE_READ_TOKENS_ABI, "readTokens", [], pinned)
    sy, pt, yt = (normalize_address(a) for a in res.value)
    if ZERO_ADDRESS in (sy, pt, yt):
        raise _data_error(
            PendleMarketReadError, res,
            f"readTokens() on {market} returned a zero address at block {pinned.number}",
        )
    expiry = client.call(pt, PT_EXPIRY_ABI, "expiry", [], pinned)
    tokens = OnchainPendleTokens(
        market_address=market,
        sy=sy,
        pt=pt,
        yt=yt,
        expiry_ts=int(expiry.value),
        block_number=pinned.number,
        ref=res.source_ref,
    )
    return tokens, expiry.evidence


def read_sy_asset_info(
    client: EthereumRpcClient, sy_address: str, pinned: PinnedBlock
) -> OnchainSyAssetInfo:
    """SY.yieldToken() and SY.assetInfo() at the pinned block.

    Only the SY's declared yield token and accounting asset: neither proves 1:1 economic
    backing or redeemability. Callers must never mark backing as verified from these reads.
    """
    sy = normalize_address(sy_address)
    yt_res = client.call(sy, SY_YIELD_TOKEN_ABI, "yieldToken", [], pinned)
    yield_token = normalize_address(yt_res.value)
    if yield_token == ZERO_ADDRESS:
        raise _data_error(SyAssetInfoError, yt_res, f"yieldToken() on {sy} returned a zero address")
    res = client.call(sy, SY_ASSET_INFO_ABI, "assetInfo", [], pinned)
    asset_type, asset, decimals = res.value
    asset = normalize_address(asset)
    if asset == ZERO_ADDRESS:
        raise _data_error(SyAssetInfoError, res, f"assetInfo() on {sy} returned a zero asset address")
    try:
        kind = SyAssetType(asset_type)
    except ValueError:
        raise _data_error(SyAssetInfoError, res, f"assetInfo() on {sy}: unknown assetType {asset_type}") from None
    return OnchainSyAssetInfo(
        sy_address=sy,
        yield_token=yield_token,
        asset_type=kind,
        asset_address=asset,
        asset_decimals=int(decimals),
        block_number=pinned.number,
        ref=res.source_ref,
        yield_token_ref=yt_res.source_ref,
    )


def verify_pt_matches_collateral(
    morpho_params: OnchainMarketParams, pendle_tokens: OnchainPendleTokens
) -> VerificationCheckDTO:
    """Morpho collateral (on-chain) must be the Pendle PT (on-chain), read at the same block."""
    if morpho_params.block_number != pendle_tokens.block_number:
        raise BlockMismatchError(
            f"Morpho read at block {morpho_params.block_number}, "
            f"Pendle read at block {pendle_tokens.block_number}"
        )
    return _check(
        "strategy.pt_is_morpho_collateral",
        normalize_address(morpho_params.collateral_token),
        SOURCE_ID,
        normalize_address(pendle_tokens.pt),
        (morpho_params.ref.evidence_id, pendle_tokens.ref.evidence_id),
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

USDC_ADDRESS = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
ERC20_DECIMALS_ABI = {
    "type": "function",
    "name": "decimals",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint8"}],
}


def _usdc_smoke(client: EthereumRpcClient, pinned: PinnedBlock) -> int:
    res = client.call(USDC_ADDRESS, ERC20_DECIMALS_ABI, "decimals", [], pinned)
    print(f"USDC decimals={res.value} raw={res.raw_return}")
    print(f"evidence_id={res.evidence.evidence_id} status={res.evidence.status.value}")
    assert res.value == 6, f"expected USDC decimals 6, got {res.value}"
    return 0


def _verify_morpho_market(client: EthereumRpcClient, pinned: PinnedBlock, market_id: str) -> int:
    # Local imports: the Morpho API is only needed for this smoke path.
    from app.adapters.morpho_api import MorphoAPIError, MorphoApiClient
    from app.ingestion.normalize import morpho_param_checks

    market_id = normalize_bytes32(market_id)
    try:
        onchain = read_morpho_market_params(client, market_id, pinned)
    finally:
        print(f"morpho_blue={MORPHO_BLUE_ADDRESS} market_id={market_id}")
    print(f"onchain evidence_id={onchain.ref.evidence_id}")

    # Filter server-side on the on-chain collateral: one page instead of the full listing.
    try:
        with MorphoApiClient() as api:
            listing = api.list_markets(collateral_token=onchain.collateral_token)
    except MorphoAPIError as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    found = next(
        ((item, raw) for item, raw in listing.iter_items()
         if isinstance(item.get("market_id"), str) and item["market_id"].lower() == market_id),
        None,
    )
    if found is None:
        print(
            f"ERROR market not in Morpho API listing (complete={listing.complete}, "
            f"{listing.stop_reason}, errors={[str(e) for e in listing.errors]})",
            file=sys.stderr,
        )
        return 1
    item, raw = found
    print(f"api evidence_id={raw.evidence_id} retrieved_at={raw.retrieved_at.isoformat()}")

    return _report(morpho_param_checks(item, SourceRef.from_response(raw), onchain))


def _report(checks: tuple[VerificationCheckDTO, ...]) -> int:
    """Print checks; non-zero exit if any is MISMATCH."""
    for c in checks:
        print(f"- {c.check}: claimed={c.claimed_value} onchain={c.onchain_value} status={c.status.value}")
    mismatched = [c.check for c in checks if c.status == VerificationStatus.MISMATCH]
    if mismatched:
        print(f"MISMATCH: {mismatched}", file=sys.stderr)
        return 1
    return 0


def _verify_pendle_market(
    client: EthereumRpcClient, pinned: PinnedBlock, market_address: str, morpho_market_id: str
) -> int:
    # Local imports: the Pendle API is only needed for this smoke path.
    from app.adapters.pendle_api import PendleAPIError, PendleApiClient
    from app.ingestion.normalize import (
        build_strategy_relations,
        pendle_sy_yield_token_check,
        pendle_token_checks,
    )

    market = normalize_address(market_address)
    print(f"pendle_market={market}")
    tokens, expiry_ev = read_pendle_market_tokens(client, market, pinned)
    print(f"readTokens evidence_id={tokens.ref.evidence_id} expiry evidence_id={expiry_ev.evidence_id}")
    sy_info = read_sy_asset_info(client, tokens.sy, pinned)
    print(f"SY yieldToken={sy_info.yield_token} evidence_id={sy_info.yield_token_ref.evidence_id}")
    print(
        f"SY accounting asset (assetInfo): type={sy_info.asset_type.name} address={sy_info.asset_address} "
        f"decimals={sy_info.asset_decimals} evidence_id={sy_info.ref.evidence_id}"
    )
    morpho = read_morpho_market_params(client, morpho_market_id, pinned)
    print(f"morpho market_id={morpho.market_id} evidence_id={morpho.ref.evidence_id}")

    try:
        with PendleApiClient() as api:
            listing = api.list_markets()
    except PendleAPIError as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    found = next(
        ((item, raw) for item, raw in listing.iter_items()
         if isinstance(item.get("address"), str) and item["address"].lower() == market),
        None,
    )
    if found is None:
        print(
            f"ERROR market not in Pendle API listing (complete={listing.complete}, "
            f"{listing.stop_reason}, errors={[str(e) for e in listing.errors]})",
            file=sys.stderr,
        )
        return 1
    item, raw = found
    print(f"api evidence_id={raw.evidence_id} retrieved_at={raw.retrieved_at.isoformat()}")

    api_ref = SourceRef.from_response(raw)
    checks = pendle_token_checks(item, api_ref, tokens)
    exit_code = _report(checks + (
        pendle_sy_yield_token_check(item, api_ref, sy_info),
        verify_pt_matches_collateral(morpho, tokens),
    ))
    print(f"chain: PT {tokens.pt} -> SY {sy_info.sy_address} -> yieldToken {sy_info.yield_token} "
          f"-> accounting asset {sy_info.asset_address}")
    print(f"note: {SY_BACKING_NOTE}")

    print("relations:")
    for r in build_strategy_relations(morpho, tokens, sy_info, client.evidence):
        print(f"- {r.source_node_id} -> {r.relation_type.value} -> {r.target_node_id} "
              f"status={r.verification_status.value} evidence={len(r.evidence_ids)}")
    return exit_code


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Ethereum RPC smoke test")
    parser.add_argument(
        "--morpho-market",
        nargs="?",
        const="",
        metavar="MARKET_ID",
        help="verify Morpho market params on-chain vs API (default id: demo manifest)",
    )
    parser.add_argument(
        "--pendle-market",
        nargs="?",
        const="",
        metavar="ADDRESS",
        help="verify Pendle tokens on-chain vs API + PT == Morpho collateral (default: demo manifest)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        client = EthereumRpcClient()
        print(f"source_id={SOURCE_ID} rpc_mode={client.mode} rpc_host={client.rpc_host}")
        print(f"chain_id={client.chain_id}")
        pinned = client.pin_block()
        print(f"pinned_block={pinned.number} hash={pinned.hash} ts={pinned.timestamp.isoformat()}")
        manifest = json.loads(_DEMO_MANIFEST.read_text())
        if args.pendle_market is not None:
            return _verify_pendle_market(
                client, pinned, args.pendle_market or manifest["pendleMarketAddress"],
                manifest["morphoMarketId"],
            )
        if args.morpho_market is not None:
            return _verify_morpho_market(client, pinned, args.morpho_market or manifest["morphoMarketId"])
        return _usdc_smoke(client, pinned)
    except EthereumRPCError as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        if exc.evidence is not None:
            print(f"evidence_id={exc.evidence.evidence_id} status={exc.evidence.status.value}",
                  file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
