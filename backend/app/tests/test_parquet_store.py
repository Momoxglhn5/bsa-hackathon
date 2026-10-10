"""ParquetStore: exact values, dedupe, missing points, separation, gaps (tmp_path, no network)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.evidence import DataMode, DataStatus
from app.models.market import PricingMethod, TokenRef
from app.models.timeseries import (
    SeriesUnit,
    SubjectKind,
    TimeseriesDTO,
    TimeseriesMetric,
    TimeseriesPointDTO,
)
from app.storage.parquet_store import (
    FLAG_GAP_BEFORE,
    FLAG_MISSING,
    Observation,
    ObservationError,
    ParquetStore,
    add_gap_flags,
    observations_from_series,
)

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
DAY = 86400
PT = "0x" + "11" * 20
USDC = TokenRef(chain_id=1, address="0x" + "22" * 20, decimals=6)
PT_ENTITY = f"asset:1:{PT}"
MORPHO_ENTITY = "morpho:1:0x" + "ab" * 32
PENDLE_ENTITY = "pendle:1:0x" + "33" * 20


@pytest.fixture
def store(tmp_path):
    return ParquetStore(tmp_path / "ts")


def obs(i=0, value="1.5", metric="MARKET_PRICE", entity=PT_ENTITY, unit="USD",
        method="API_SPOT", source="pendle_api", retrieved=NOW, flags=()):
    return Observation(
        entity_id=entity, metric=metric, ts_utc=T0 + timedelta(days=i),
        value_decimal=Decimal(value) if value is not None else None, unit=unit,
        source_id=source, retrieved_at=retrieved, quality_flags=flags, pricing_method=method,
    )


def implied_apy_series() -> TimeseriesDTO:
    return TimeseriesDTO(
        chain_id=1, data_mode=DataMode.REAL, series_id="ts_pendle_pt_implied_apy_day_test",
        metric=TimeseriesMetric.PT_IMPLIED_APY, subject_kind=SubjectKind.MARKET,
        subject_id="0x" + "33" * 20, unit=SeriesUnit.RATIO, interval_seconds=DAY,
        source_id="pendle_api", status=DataStatus.PARTIAL,
        points=(
            TimeseriesPointDTO(timestamp=T0, value=Decimal("0.0512345678901234567890"), status=DataStatus.OK),
            TimeseriesPointDTO(timestamp=T0 + timedelta(days=1), value=None, status=DataStatus.MISSING),
            TimeseriesPointDTO(timestamp=T0 + timedelta(days=2), value=Decimal("1E-7"), status=DataStatus.OK),
        ),
    )


def test_round_trip_keeps_decimal_precision(store):
    dto = implied_apy_series()
    store.write_observations(observations_from_series(dto, PENDLE_ENTITY, NOW), DataMode.REAL)
    rows = store.read_observations(PENDLE_ENTITY, "PT_IMPLIED_APY", DataMode.REAL)
    assert rows[0].value_decimal == Decimal("0.0512345678901234567890")
    assert str(rows[0].value_decimal) == "0.0512345678901234567890"
    assert rows[2].value_decimal == Decimal("1E-7")

    series = store.read_series(PENDLE_ENTITY, "PT_IMPLIED_APY", DataMode.REAL, interval_seconds=DAY)
    assert [p.value for p in series.points] == [p.value for p in dto.points]
    assert [p.status for p in series.points] == [p.status for p in dto.points]
    assert series.status == DataStatus.PARTIAL


def test_same_rows_twice_no_duplicates(store):
    rows = [obs(i) for i in range(3)]
    store.write_observations(rows, DataMode.REAL)
    store.write_observations(rows, DataMode.REAL)
    assert len(store.read_observations(PT_ENTITY, "MARKET_PRICE", DataMode.REAL)) == 3


def test_dedupe_keeps_latest_retrieval(store):
    store.write_observations([obs(0, "1.0", retrieved=NOW)], DataMode.REAL)
    store.write_observations([obs(0, "2.0", retrieved=NOW - timedelta(hours=1))], DataMode.REAL)
    store.write_observations([obs(0, "3.0", retrieved=NOW + timedelta(hours=1))], DataMode.REAL)
    assert [r.value_decimal for r in store.read_observations(PT_ENTITY, "MARKET_PRICE", DataMode.REAL)] == [
        Decimal("3.0")
    ]


def test_missing_point_stays_null(store):
    store.write_observations(observations_from_series(implied_apy_series(), PENDLE_ENTITY, NOW), DataMode.REAL)
    missing = store.read_observations(PENDLE_ENTITY, "PT_IMPLIED_APY", DataMode.REAL)[1]
    assert missing.value_decimal is None
    assert FLAG_MISSING in missing.quality_flags


def test_null_without_flag_rejected(store):
    with pytest.raises(ObservationError):
        store.write_observations([obs(0, None)], DataMode.REAL)


def test_future_leakage_rejected(store):
    with pytest.raises(ObservationError, match="future leakage"):
        store.write_observations([obs(0, retrieved=T0 - timedelta(seconds=1))], DataMode.REAL)


def test_naive_timestamp_rejected(store):
    naive = Observation(PT_ENTITY, "MARKET_PRICE", datetime(2026, 10, 1), Decimal(1), "USD",
                        "pendle_api", NOW, pricing_method="API_SPOT")
    with pytest.raises(ObservationError):
        store.write_observations([naive], DataMode.REAL)


def test_unit_not_allowed_rejected(store):
    with pytest.raises(ObservationError):
        store.write_observations([obs(0, unit="RATIO")], DataMode.REAL)


def test_mixed_metrics_in_one_write_rejected(store):
    with pytest.raises(ObservationError):
        store.write_observations([obs(0), obs(1, metric="PT_EXIT_EXECUTION_PRICE", unit="TOKEN",
                                              method="EXECUTION_QUOTE")], DataMode.REAL)


def test_price_metrics_are_separate(store):
    market = [obs(i, "0.98") for i in range(2)]
    exit_ = [obs(i, "0.97", metric="PT_EXIT_EXECUTION_PRICE", unit="TOKEN", method="EXECUTION_QUOTE",
                 source="pendle_sdk_swapping_prices") for i in range(2)]
    oracle = [obs(i, "0.99", metric="ORACLE_PRICE", entity=MORPHO_ENTITY, unit="TOKEN",
                  method="MORPHO_ORACLE", source="ethereum_rpc") for i in range(2)]
    paths = {
        store.write_observations(market, DataMode.REAL),
        store.write_observations(exit_, DataMode.REAL),
        store.write_observations(oracle, DataMode.REAL),
    }
    assert len(paths) == 3
    assert {r.value_decimal for r in store.read_observations(PT_ENTITY, "MARKET_PRICE", DataMode.REAL)} == {
        Decimal("0.98")
    }
    assert {r.metric for r in store.read_observations(PT_ENTITY, "PT_EXIT_EXECUTION_PRICE", DataMode.REAL)} == {
        "PT_EXIT_EXECUTION_PRICE"
    }
    assert store.read_observations(PT_ENTITY, "ORACLE_PRICE", DataMode.REAL) == []
    assert store.read_observations(MORPHO_ENTITY, "MARKET_PRICE", DataMode.REAL) == []

    exit_series = store.read_series(PT_ENTITY, "PT_EXIT_EXECUTION_PRICE", DataMode.REAL, quote_asset=USDC)
    assert exit_series.pricing_method == PricingMethod.EXECUTION_QUOTE
    assert exit_series.status == DataStatus.OK

    oracle_series = store.read_series(MORPHO_ENTITY, "ORACLE_PRICE", DataMode.REAL, quote_asset=USDC)
    assert oracle_series.subject_id == "0x" + "ab" * 32  # long bytes32 id -> hashed series_id


def test_gap_flagged_not_filled(store):
    rows = [obs(0), obs(1), obs(4), obs(5)]
    flagged = add_gap_flags(rows, DAY)
    assert len(flagged) == 4
    assert [FLAG_GAP_BEFORE in r.quality_flags for r in flagged] == [False, False, True, False]
    assert not any(r.is_interpolated for r in flagged)

    store.write_observations(rows, DataMode.REAL, expected_interval_seconds=DAY)
    stored = store.read_observations(PT_ENTITY, "MARKET_PRICE", DataMode.REAL)
    assert [r.ts_utc for r in stored] == [r.ts_utc for r in rows]
    assert FLAG_GAP_BEFORE in stored[2].quality_flags

    # backfilling the gap clears the flag on the merged series
    store.write_observations([obs(2), obs(3)], DataMode.REAL, expected_interval_seconds=DAY)
    stored = store.read_observations(PT_ENTITY, "MARKET_PRICE", DataMode.REAL)
    assert len(stored) == 6 and not any(FLAG_GAP_BEFORE in r.quality_flags for r in stored)


def test_real_and_demo_never_mix(store):
    store.write_observations([obs(0, "1.0")], DataMode.REAL)
    store.write_observations([obs(0, "9.0"), obs(1, "9.0")], DataMode.DEMO)
    assert [r.value_decimal for r in store.read_observations(PT_ENTITY, "MARKET_PRICE", DataMode.REAL)] == [
        Decimal("1.0")
    ]
    assert len(store.read_observations(PT_ENTITY, "MARKET_PRICE", DataMode.DEMO)) == 2
    assert store.read_series(PT_ENTITY, "MARKET_PRICE", DataMode.DEMO).data_mode == DataMode.DEMO


def test_unknown_series_is_empty(store):
    assert store.read_observations(PT_ENTITY, "MARKET_PRICE", DataMode.REAL) == []
    assert store.read_series(PT_ENTITY, "MARKET_PRICE", DataMode.REAL) is None
