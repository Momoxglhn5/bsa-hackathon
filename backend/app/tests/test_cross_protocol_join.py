"""Join on chain and address only, never on token labels."""
from datetime import timedelta
import pytest
from app.ingestion.join import find_candidate_strategies, JoinError
from app.ingestion.normalize import normalize_morpho_market, normalize_pendle_market, TokenMeta
from app.models.evidence import DataMode
from app.models.strategy import StrategyStatus


def markets(morpho_item, pendle_item, api_ref, collateral_symbol="SAME", pt_symbol="SAME"):
    collateral = morpho_item["collateral_token"]
    pt = pendle_item["pt"].split("-", 1)[1]
    m = normalize_morpho_market(morpho_item, api_ref, tokens={
        collateral: TokenMeta(collateral, 18, collateral_symbol, api_ref)}).dto
    p = normalize_pendle_market(pendle_item, api_ref, tokens={
        pt: TokenMeta(pt, 18, pt_symbol, api_ref)}).dto
    return m, p


def test_same_symbol_different_addresses_do_not_join(morpho_item, pendle_item, api_ref):
    pendle_item["pt"] = "1-0x" + "99" * 20
    m, p = markets(morpho_item, pendle_item, api_ref)
    result = find_candidate_strategies([m], [p])
    assert not result.candidates and not result.expired


def test_same_address_different_symbols_join_as_candidate(morpho_item, pendle_item, api_ref):
    m, p = markets(morpho_item, pendle_item, api_ref, "LABEL-A", "LABEL-B")
    result = find_candidate_strategies([m], [p])
    assert len(result.candidates) == 1
    assert result.candidates[0].strategy.status == StrategyStatus.CANDIDATE


def test_other_chain_cannot_join(morpho_item, pendle_item, api_ref):
    m, p = markets(morpho_item, pendle_item, api_ref)
    # Deliberately bypass the mainnet-only DTO validator to exercise the join's guard.
    other_chain = p.model_copy(update={"chain_id": 8453})
    assert not find_candidate_strategies([m], [other_chain]).candidates


def test_duplicate_market_has_one_stable_strategy(morpho_item, pendle_item, api_ref):
    m, p = markets(morpho_item, pendle_item, api_ref)
    one = find_candidate_strategies([m], [p]).candidates[0]
    repeated = find_candidate_strategies([m, m], [p])
    assert len(repeated.candidates) == 1 and repeated.anomalies
    assert repeated.candidates[0].strategy.strategy_id == one.strategy.strategy_id


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_expiry_boundary(morpho_item, pendle_item, api_ref, offset):
    m, p = markets(morpho_item, pendle_item, api_ref)
    result = find_candidate_strategies([m], [p], as_of=p.pendle.expiry + timedelta(seconds=offset))
    if offset < 0:
        assert len(result.candidates) == 1 and not result.expired
    else:
        assert not result.candidates and result.expired[0].strategy.status == StrategyStatus.EXPIRED


def test_real_demo_join_rejected(morpho_item, pendle_item, api_ref):
    m, p = markets(morpho_item, pendle_item, api_ref)
    with pytest.raises(JoinError):
        find_candidate_strategies([m], [p.model_copy(update={"data_mode": DataMode.DEMO})])
