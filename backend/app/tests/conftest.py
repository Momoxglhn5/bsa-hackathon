"""Small synthetic API payloads shared by the ingestion regression tests."""
from datetime import datetime, timezone
import pytest
from app.ingestion.normalize import SourceRef

@pytest.fixture
def api_ref():
    return SourceRef("test_api", "ev_" + "a" * 24, datetime(2026, 10, 10, tzinfo=timezone.utc))

@pytest.fixture
def morpho_item():
    return dict(chain_id=1, market_id="0x" + "ab" * 32,
                collateral_token="0x" + "11" * 20, loan_token="0x" + "22" * 20,
                oracle_address="0x" + "44" * 20, irm_address="0x" + "55" * 20,
                lltv_wad="860000000000000000")

@pytest.fixture
def pendle_item():
    return dict(chainId=1, address="0x" + "33" * 20,
                pt="1-0x" + "11" * 20, yt="1-0x" + "66" * 20,
                sy="1-0x" + "77" * 20, underlyingAsset="1-0x" + "88" * 20,
                expiry="2027-01-07T00:00:00Z", details={"impliedApy": "0.05"})
