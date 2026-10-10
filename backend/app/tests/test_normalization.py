"""Exact units, missing values and historical ordering."""
from decimal import Decimal
import pytest
from app.ingestion.normalize import (
    asset_id, morpho_market_key, pendle_market_key, normalize_morpho_market,
    normalize_pendle_market, normalize_pendle_history, OnchainPendleTokens,
    OracleRead, oracle_price, MissingReason,
)
from app.models.evidence import DataStatus, scale_raw
from app.models.market import TokenRef
from app.models.timeseries import TimeseriesMetric


def test_chain_is_part_of_asset_identity():
    address = "0x" + "ab" * 20
    assert asset_id(address, 1) != asset_id(address, 8453)
    assert asset_id("0x" + "AB" * 20) == asset_id(address)


def test_market_id_is_not_contract_address(morpho_item):
    identifier = morpho_item["market_id"]
    assert morpho_market_key(identifier) == "morpho:1:" + identifier
    with pytest.raises(ValueError):
        pendle_market_key(identifier)
    with pytest.raises(ValueError):
        morpho_market_key("0x" + "ab" * 20)


@pytest.mark.parametrize("raw,decimals,expected", [
    (1234567, 6, "1.234567"), (1000000000000000001, 18, "1.000000000000000001"),
    (0, 6, "0"), (42, 0, "42"),
])
def test_raw_amount_precision(raw, decimals, expected):
    assert scale_raw(raw, decimals) == Decimal(expected)


def test_apy_is_ratio_and_missing_is_not_zero(pendle_item, api_ref):
    out = normalize_pendle_market(pendle_item, api_ref)
    assert out.dto.pendle.implied_apy == Decimal("0.05")
    assert out.dto.pendle.underlying_apy is None
    assert "pendle.underlying_apy" in out.missing
    pendle_item["details"]["underlyingApy"] = "0"
    assert normalize_pendle_market(pendle_item, api_ref).dto.pendle.underlying_apy == Decimal(0)


def test_verified_market_with_missing_metrics_is_partial(pendle_item, api_ref):
    chain = OnchainPendleTokens(pendle_item["address"], pendle_item["sy"][2:],
        pendle_item["pt"][2:], pendle_item["yt"][2:], 1799280000, 123, api_ref)
    out = normalize_pendle_market(pendle_item, api_ref, onchain=chain)
    assert out.dto.status == DataStatus.PARTIAL
    assert out.dto.pendle.liquidity_usd is None


def test_morpho_missing_state_not_fabricated(morpho_item, api_ref):
    out = normalize_morpho_market(morpho_item, api_ref)
    assert out.dto.morpho.total_supply_assets is None
    assert out.dto.morpho.total_borrow_assets is None
    assert out.dto.morpho.lltv == Decimal("0.86")


def test_oracle_decimal_conversion(api_ref):
    collateral = TokenRef(chain_id=1, address="0x" + "11" * 20, decimals=18)
    loan = TokenRef(chain_id=1, address="0x" + "22" * 20, decimals=6)
    read = OracleRead("0x" + "44" * 20, 963701707572298326000000, 123, api_ref)
    price, missing = oracle_price(read, collateral, loan)
    assert missing is None and price.scale_exponent == 24
    assert price.value == Decimal("0.963701707572298326")
    assert oracle_price(read, collateral.model_copy(update={"decimals": None}), loan) == (
        None, MissingReason.DECIMALS_UNKNOWN)


def test_history_sorted_deduplicated_and_missing_preserved(pendle_item, api_ref):
    payload = {"results": [
        {"timestamp": "2026-10-09T00:00:00Z", "impliedApy": "0.05"},
        {"timestamp": "2026-10-08T00:00:00Z", "impliedApy": None},
        {"timestamp": "2026-10-09T00:00:00Z", "impliedApy": "0.05"},
    ]}
    out = normalize_pendle_history(payload, pendle_item["address"], api_ref, time_frame="day")[TimeseriesMetric.PT_IMPLIED_APY]
    assert out.dropped_duplicates == 1 and out.missing_points == 1
    assert out.dto.status == DataStatus.PARTIAL
    assert len(out.dto.points) == 2
    assert out.dto.points[0].timestamp < out.dto.points[1].timestamp
    assert out.dto.points[0].value is None
    assert out.dto.points[1].value == Decimal("0.05")
