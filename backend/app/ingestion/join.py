"""Join normalised Morpho Blue and Pendle V2 markets into candidate strategies.

Match rule (and the only one):
    morpho.chain_id == pendle.chain_id
    AND normalize(morpho.collateral_token.address) == normalize(pendle.pt.address)

Symbols are never read here. Loan-asset preference (USDC by default, identified by
address) only ranks or filters candidates; it never creates or implies a match.

Output strategies are CANDIDATE (or EXPIRED when the PT expiry is already reached,
which the contract requires). Only `promote_strategy`, fed with on-chain checks,
can mark a strategy VERIFIED_ACTIVE.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from app.models.evidence import (
    DataMode,
    DataStatus,
    VerificationCheckDTO,
    VerificationStatus,
    normalize_address,
)
from app.models.market import MarketDTO, Protocol, TokenRef
from app.models.strategy import StrategyDTO, StrategyStatus, StrategyType, make_strategy_id

USDC_MAINNET = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
DEFAULT_PREFERRED_LOAN_ASSETS = frozenset({(1, USDC_MAINNET)})

# Source id of app.adapters.ethereum_rpc (not imported: this module stays network-free).
RPC_SOURCE_ID = "ethereum_rpc"

CANDIDATE_REASON = "address match only (Morpho collateral == Pendle PT); on-chain strategy verification pending"

# Worst first. A strategy is never better than the worst of its two markets.
_STATUS_SEVERITY = {
    DataStatus.ERROR: 0,
    DataStatus.MISMATCH: 1,
    DataStatus.MISSING: 2,
    DataStatus.STALE: 3,
    DataStatus.UNVERIFIED: 4,
    DataStatus.PARTIAL: 5,
    DataStatus.OK: 6,
}


class JoinError(ValueError):
    """Inputs cannot be joined (wrong protocol, mixed REAL/DEMO data)."""


@dataclass(frozen=True)
class JoinCandidate:
    strategy: StrategyDTO
    morpho_market_key: str
    pendle_market_key: str | None
    pt_asset_id: str
    loan_asset_id: str
    preferred_loan: bool


@dataclass(frozen=True)
class JoinResult:
    candidates: tuple[JoinCandidate, ...]   # status CANDIDATE, ranked
    expired: tuple[JoinCandidate, ...]      # status EXPIRED (PT expiry <= as_of)
    anomalies: tuple[str, ...]
    morpho_markets_seen: int
    pendle_markets_seen: int
    filtered_out_by_loan_asset: int


def _worst_status(*statuses: DataStatus) -> DataStatus:
    return min(statuses, key=lambda s: _STATUS_SEVERITY[s])


def _candidate_data_status(morpho: MarketDTO, pendle: MarketDTO) -> DataStatus:
    """Worst of both markets; OK is capped at UNVERIFIED since the join itself is unverified."""
    status = _worst_status(morpho.status, pendle.status)
    return DataStatus.UNVERIFIED if status == DataStatus.OK else status


def _pick_token(a: TokenRef, b: TokenRef) -> TokenRef:
    """Same address on both sides; keep the reference that carries known decimals."""
    return a if a.decimals is not None or b.decimals is None else b


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _check_inputs(morpho_markets: list[MarketDTO], pendle_markets: list[MarketDTO]) -> DataMode | None:
    for m in morpho_markets:
        if m.protocol != Protocol.MORPHO_BLUE or m.morpho is None:
            raise JoinError(f"{m.market_key}: expected a Morpho Blue market")
    for p in pendle_markets:
        if p.protocol != Protocol.PENDLE_V2 or p.pendle is None:
            raise JoinError(f"{p.market_key}: expected a Pendle V2 market")
    modes = {m.data_mode for m in morpho_markets + pendle_markets}
    if len(modes) > 1:
        raise JoinError(f"REAL and DEMO markets cannot be joined together: {sorted(x.value for x in modes)}")
    return next(iter(modes), None)


def find_candidate_strategies(
    morpho_markets: Iterable[MarketDTO],
    pendle_markets: Iterable[MarketDTO],
    *,
    as_of: datetime | None = None,
    preferred_loan_assets: frozenset[tuple[int, str]] = DEFAULT_PREFERRED_LOAN_ASSETS,
    only_preferred_loan: bool = False,
) -> JoinResult:
    """Candidate Morpho × Pendle strategies, matched on chain_id + collateral/PT address.

    as_of: reference time for the expiry check; defaults to the later `as_of` of each pair.
    preferred_loan_assets: (chain_id, address) pairs used for ranking, or as a filter
        when `only_preferred_loan` is True.
    """
    morpho_list = list(morpho_markets)
    pendle_list = list(pendle_markets)
    data_mode = _check_inputs(morpho_list, pendle_list)
    preferred = {(c, normalize_address(a)) for c, a in preferred_loan_assets}
    anomalies: list[str] = []

    pt_index: dict[tuple[int, str], list[MarketDTO]] = {}
    for p in pendle_list:
        key = (p.chain_id, normalize_address(p.pendle.pt.address))
        pt_index.setdefault(key, []).append(p)
    for (chain_id, pt), markets in pt_index.items():
        if len(markets) > 1:
            anomalies.append(
                f"PT {pt} (chain {chain_id}) appears in {len(markets)} Pendle markets: "
                + ", ".join(m.market_id for m in markets)
            )

    candidates: list[JoinCandidate] = []
    expired: list[JoinCandidate] = []
    filtered_out = 0
    seen_ids: set[str] = set()

    for m in morpho_list:
        collateral = m.morpho.collateral_token
        key = (m.chain_id, normalize_address(collateral.address))
        matches = pt_index.get(key)
        if not matches:
            continue

        loan = m.morpho.loan_token
        is_preferred = (loan.chain_id, normalize_address(loan.address)) in preferred
        if only_preferred_loan and not is_preferred:
            filtered_out += 1
            continue

        ambiguous = len(matches) > 1
        p = matches[0]
        pendle_fields = p.pendle
        pt = _pick_token(pendle_fields.pt, collateral)
        reference_time = as_of or max(m.as_of, p.as_of)
        is_expired = pendle_fields.expiry <= reference_time

        strategy_id = make_strategy_id(m.chain_id, m.market_id, pt.address)
        if strategy_id in seen_ids:
            anomalies.append(f"duplicate Morpho market {m.market_id} skipped")
            continue
        seen_ids.add(strategy_id)

        if ambiguous:
            # Several Pendle markets share this PT: do not pick one.
            pendle_address, expiry, underlying = None, None, None
            reason = "PT listed in several Pendle markets; Pendle market not selected"
            status = StrategyStatus.CANDIDATE
            refs = [m]
        else:
            pendle_address = pendle_fields.market_address
            expiry = pendle_fields.expiry
            underlying = pendle_fields.underlying_asset
            reason = "PT expiry reached" if is_expired else CANDIDATE_REASON
            status = StrategyStatus.EXPIRED if is_expired else StrategyStatus.CANDIDATE
            refs = [m, p]

        data_status = (
            _candidate_data_status(m, p) if not ambiguous
            else (DataStatus.UNVERIFIED if m.status == DataStatus.OK else m.status)
        )
        strategy = StrategyDTO(
            chain_id=m.chain_id,
            data_mode=data_mode,
            strategy_id=strategy_id,
            strategy_type=StrategyType.PENDLE_PT_COLLATERAL_ON_MORPHO,
            status=status,
            status_reason=reason,
            data_status=data_status,
            morpho_market_id=m.market_id,
            pendle_market_address=pendle_address,
            pt=pt,
            loan_asset=loan,
            underlying_asset=underlying,
            expiry=expiry,
            as_of=reference_time,
            block_number=m.block_number if ambiguous or m.block_number == p.block_number else None,
            verification=(),
            source_ids=_dedupe(s for r in refs for s in r.source_ids),
            evidence_ids=_dedupe(e for r in refs for e in r.evidence_ids),
        )
        candidate = JoinCandidate(
            strategy=strategy,
            morpho_market_key=m.market_key,
            pendle_market_key=None if ambiguous else p.market_key,
            pt_asset_id=pt.asset_id,
            loan_asset_id=loan.asset_id,
            preferred_loan=is_preferred,
        )
        (expired if status == StrategyStatus.EXPIRED else candidates).append(candidate)

    def rank(c: JoinCandidate) -> tuple:
        return (not c.preferred_loan, -_STATUS_SEVERITY[c.strategy.data_status], c.strategy.morpho_market_id)

    return JoinResult(
        candidates=tuple(sorted(candidates, key=rank)),
        expired=tuple(sorted(expired, key=rank)),
        anomalies=tuple(anomalies),
        morpho_markets_seen=len(morpho_list),
        pendle_markets_seen=len(pendle_list),
        filtered_out_by_loan_asset=filtered_out,
    )

def promote_strategy(
    candidate: StrategyDTO,
    checks: tuple[VerificationCheckDTO, ...],
    block_number: int | None,
    evidence_ids: tuple[str, ...],
    onchain_expiry: datetime | None,
    data_available: bool,
    data_problems: list[str],
    now: datetime,
    min_days_to_expiry: int,
) -> StrategyDTO:
    """New StrategyDTO whose status follows the on-chain checks. Pure; input untouched.

    Status is decided only from verification checks (addresses / raw values), the pinned
    block and the on-chain PT expiry — never from symbols.
    """
    mismatched = [c.check for c in checks if c.status == VerificationStatus.MISMATCH]
    unverified = [c.check for c in checks if c.status != VerificationStatus.VERIFIED]
    incomplete = [
        *(["no checks"] if not checks else []),
        *([f"not verified: {', '.join(unverified)}"] if unverified else []),
        *(["no pinned block"] if block_number is None else []),
        *(["no on-chain expiry"] if onchain_expiry is None else []),
    ]
    # On-chain expiry when read; the API expiry only keeps the contract's expiry rule honest.
    expiry = onchain_expiry if onchain_expiry is not None else candidate.expiry
    expired = expiry is not None and expiry <= now

    if mismatched:
        status, data_status = StrategyStatus.UNSUPPORTED, DataStatus.MISMATCH
        reason = f"on-chain verification failed: {', '.join(mismatched)}"
    elif expired:
        status = StrategyStatus.EXPIRED
        data_status = DataStatus.UNVERIFIED if incomplete else DataStatus.OK
        reason = "PT expiry reached (on-chain)" if onchain_expiry is not None else "PT expiry reached (API)"
    elif incomplete:
        status, data_status = StrategyStatus.CANDIDATE, DataStatus.UNVERIFIED
        reason = f"on-chain verification incomplete: {'; '.join(incomplete)}"
    elif expiry <= now + timedelta(days=min_days_to_expiry):
        # Verified, but a CANDIDATE may not be OK: PARTIAL = essentials verified.
        status, data_status = StrategyStatus.CANDIDATE, DataStatus.PARTIAL
        reason = f"verified on-chain but PT expiry {expiry.isoformat()} within {min_days_to_expiry} days"
    elif data_available:
        status, data_status, reason = StrategyStatus.VERIFIED_ACTIVE, DataStatus.OK, None
    else:
        status, data_status = StrategyStatus.CANDIDATE, DataStatus.PARTIAL
        reason = f"verified on-chain; data insufficient: {'; '.join(data_problems) or 'unspecified'}"

    source_ids = candidate.source_ids + ((RPC_SOURCE_ID,) if block_number is not None and evidence_ids else ())
    return StrategyDTO.model_validate({
        **candidate.model_dump(),
        "status": status,
        "status_reason": reason[:512] if reason else None,
        "data_status": data_status,
        "expiry": expiry,
        "as_of": now,
        "block_number": block_number,
        "verification": checks,
        "source_ids": _dedupe(source_ids),
        "evidence_ids": _dedupe((*candidate.evidence_ids, *evidence_ids)),
    })
