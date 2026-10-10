"""promote_strategy: on-chain checks decide the strategy status (no network)."""
from datetime import datetime, timedelta, timezone

from app.ingestion.join import promote_strategy
from app.models.evidence import DataMode, DataStatus, VerificationCheckDTO, VerificationStatus
from app.models.market import TokenRef
from app.models.strategy import StrategyDTO, StrategyStatus, make_strategy_id

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
MARKET_ID = "0x" + "ab" * 32
PT = "0x" + "11" * 20
LOAN = "0x" + "22" * 20
API_EV = "ev_" + "a" * 24
RPC_EV = "ev_" + "b" * 24


def candidate() -> StrategyDTO:
    return StrategyDTO(
        chain_id=1,
        data_mode=DataMode.REAL,
        strategy_id=make_strategy_id(1, MARKET_ID, PT),
        status=StrategyStatus.CANDIDATE,
        status_reason="address match only",
        data_status=DataStatus.UNVERIFIED,
        morpho_market_id=MARKET_ID,
        pendle_market_address="0x" + "33" * 20,
        pt=TokenRef(chain_id=1, address=PT),
        loan_asset=TokenRef(chain_id=1, address=LOAN),
        expiry=NOW + timedelta(days=90),
        as_of=NOW - timedelta(hours=1),
        source_ids=("morpho_api", "pendle_api"),
        evidence_ids=(API_EV,),
    )


def check(name: str, onchain: str = PT, claimed: str = PT) -> VerificationCheckDTO:
    status = VerificationStatus.VERIFIED if onchain == claimed else VerificationStatus.MISMATCH
    return VerificationCheckDTO(
        check=name, claimed_value=claimed, claimed_source_id="morpho_api",
        onchain_value=onchain, status=status, evidence_ids=(RPC_EV,),
    )


CHECKS = (check("morpho.collateral_token"), check("strategy.pt_is_morpho_collateral"))


def promote(**overrides):
    args = dict(
        candidate=candidate(), checks=CHECKS, block_number=123, evidence_ids=(RPC_EV, API_EV),
        onchain_expiry=NOW + timedelta(days=90), data_available=True, data_problems=[],
        now=NOW, min_days_to_expiry=7,
    )
    args.update(overrides)
    return promote_strategy(**args)


def test_all_verified_is_active():
    s = promote()
    assert s.status == StrategyStatus.VERIFIED_ACTIVE
    assert s.data_status == DataStatus.OK
    assert s.block_number == 123
    assert s.evidence_ids == (API_EV, RPC_EV)
    assert "ethereum_rpc" in s.source_ids


def test_onchain_expiry_replaces_api_expiry():
    onchain = NOW + timedelta(days=60)
    assert promote(onchain_expiry=onchain).expiry == onchain


def test_mismatch_is_unsupported():
    bad = (CHECKS[0], check("strategy.pt_is_morpho_collateral", onchain="0x" + "99" * 20))
    s = promote(checks=bad)
    assert s.status == StrategyStatus.UNSUPPORTED
    assert s.data_status == DataStatus.MISMATCH
    assert "strategy.pt_is_morpho_collateral" in s.status_reason


def test_missing_block_stays_candidate():
    s = promote(block_number=None)
    assert s.status == StrategyStatus.CANDIDATE
    assert s.data_status == DataStatus.UNVERIFIED
    assert s.status_reason.startswith("on-chain verification incomplete")


def test_no_checks_stays_candidate():
    assert promote(checks=()).status == StrategyStatus.CANDIDATE


def test_expired():
    s = promote(onchain_expiry=NOW - timedelta(seconds=1))
    assert s.status == StrategyStatus.EXPIRED


def test_too_close_to_expiry_stays_candidate():
    s = promote(onchain_expiry=NOW + timedelta(days=3))
    assert s.status == StrategyStatus.CANDIDATE
    assert "within 7 days" in s.status_reason


def test_data_missing_stays_candidate_partial():
    s = promote(data_available=False, data_problems=["pendle impliedApy missing"])
    assert s.status == StrategyStatus.CANDIDATE
    assert s.data_status == DataStatus.PARTIAL
    assert s.status_reason == "verified on-chain; data insufficient: pendle impliedApy missing"


def test_input_not_mutated():
    c = candidate()
    before = c.model_dump()
    promote(candidate=c)
    assert c.model_dump() == before
    assert c.status == StrategyStatus.CANDIDATE
