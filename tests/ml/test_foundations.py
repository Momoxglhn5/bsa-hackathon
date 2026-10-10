"""SYNTHETIC fixtures only: no real market, model run or predictive validation."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.app.forecasting.cache import ForecastCache, cache_path, export_bundle
from backend.app.forecasting.contracts import ForecastBundle
from backend.app.models.strategy import StrategyDTO, make_strategy_id
from backend.app.models.timeseries import TimeseriesDTO
from ml.benchmark import backtest, persistence, rolling_mean
from ml.series import SeriesUnavailable, prepare_pt_series

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
PT = "0x" + "a" * 40
MARKET = "0x" + "b" * 40
MORPHO = "0x" + "c" * 64
EVIDENCE = "ev_" + "d" * 24


def strategy_payload():
    return dict(
        data_mode="DEMO", chain_id=1,
        strategy_id=make_strategy_id(1, MORPHO, PT),
        status="CANDIDATE", data_status="UNVERIFIED",
        morpho_market_id=MORPHO, pendle_market_address=MARKET,
        pt=dict(chain_id=1, address=PT, decimals=18),
        loan_asset=dict(chain_id=1, address="0x" + "e" * 40, decimals=6),
        expiry=NOW + timedelta(days=30), as_of=NOW,
        source_ids=["synthetic_fixture"],
    )


def strategy(real=False):
    payload = strategy_payload()
    if real:
        # Exercise REAL contract gates with synthetic addresses; not a live strategy.
        payload.update(data_mode="REAL", status="VERIFIED_ACTIVE", data_status="OK",
                       block_number=123, evidence_ids=[EVIDENCE],
                       verification=[dict(check="synthetic_fixture", onchain_value=PT,
                                          status="VERIFIED", evidence_ids=[EVIDENCE])])
    return StrategyDTO.model_validate(payload)


def series_payload(n=66):
    return dict(
        data_mode="DEMO", chain_id=1, series_id="ts_synthetic_pt",
        metric="MARKET_PRICE", subject_kind="ASSET", subject_id=PT,
        unit="USD", pricing_method="API_SPOT", interval_seconds=86400,
        source_id="synthetic_fixture", status="OK",
        points=[dict(timestamp=NOW - timedelta(days=n - i - 1),
                     value=str(Decimal("0.90") + Decimal(i) / 1000),
                     status="OK", evidence_ids=[EVIDENCE]) for i in range(n)],
    )


def prepared(payload=None, strat=None):
    return prepare_pt_series(
        TimeseriesDTO.model_validate(payload or series_payload()), strat or strategy(),
        source_snapshot_ref="SYNTHETIC_TEST_ONLY", source_as_of=NOW, retrieved_at=NOW,
    )


def bundle_payload():
    return dict(
        forecast_id="forecast:SYNTHETIC_TEST_ONLY", strategy_id=strategy().strategy_id,
        entity_id=f"pendle:1:{MARKET}", model_version="SYNTHETIC_TEST_ONLY",
        points=[dict(horizon_days=h) for h in (1, 3, 7)],
        limitations=["Synthetic contract fixture, not a model result"],
    )


def validated_fixture():
    payload = bundle_payload()
    payload.update(status="VALIDATED", generated_at=NOW, latest_observation_at=NOW,
                   training_cutoff_at=NOW, source_snapshot_ref="SYNTHETIC_TEST_ONLY",
                   backtest=dict(n_evaluations=9, mae_model="0.01", mae_naive="0.02",
                                 rmse_model="0.015", coverage_q10_q90="0.8",
                                 validation_status="VALIDATED"),
                   points=[dict(horizon_days=h, q10="0.9", q50="0.95", q90="1")
                           for h in (1, 3, 7)])
    return payload


def test_mapping_exact_pt_and_provenance():
    data = prepared()
    assert data.entity_id == f"pendle:1:{MARKET}"
    assert data.target_metric == "PT_MARKET_PRICE_USD"
    assert data.evidence_ids == (EVIDENCE,)
    assert data.data_mode.value == "DEMO"


def test_short_history_requires_explicit_research_and_preserves_evidence_checks():
    short = series_payload(31)
    with pytest.raises(SeriesUnavailable, match="INSUFFICIENT_HISTORY"):
        prepared(short)
    kwargs = dict(source_snapshot_ref="SYNTHETIC_TEST_ONLY", source_as_of=NOW,
                  retrieved_at=NOW, min_observations=14, exploratory=True)
    result = prepare_pt_series(TimeseriesDTO.model_validate(short), strategy(), **kwargs)
    assert len(result.values) == 31
    short['points'][0]['evidence_ids'] = []
    with pytest.raises(SeriesUnavailable, match="MISSING_POINT_EVIDENCE"):
        prepare_pt_series(TimeseriesDTO.model_validate(short), strategy(), **kwargs)


@pytest.mark.parametrize("mutation", ["short", "gap", "wrong_pt", "hourly", "missing_evidence", "nonpositive"])
def test_unusable_series(mutation):
    payload = series_payload(44 if mutation == "short" else 66)
    if mutation == "gap":
        del payload["points"][10]
    elif mutation == "wrong_pt":
        payload["subject_id"] = MARKET
    elif mutation == "hourly":
        payload["interval_seconds"] = 3600
    elif mutation == "missing_evidence":
        payload["points"][1]["evidence_ids"] = []
    elif mutation == "nonpositive":
        payload["points"][1]["value"] = "0"
    with pytest.raises(SeriesUnavailable):
        prepared(payload)


def test_expiry_guard():
    payload = strategy_payload()
    payload["expiry"] = NOW + timedelta(days=7)
    with pytest.raises(SeriesUnavailable, match="EXPIRY"):
        prepared(strat=StrategyDTO.model_validate(payload))


def test_observation_after_source_timestamp():
    with pytest.raises(SeriesUnavailable, match="OBSERVATION_AFTER"):
        prepare_pt_series(TimeseriesDTO.model_validate(series_payload()), strategy(),
                          source_snapshot_ref="SYNTHETIC", source_as_of=NOW-timedelta(days=1),
                          retrieved_at=NOW)


def test_benchmark_no_future_in_context_and_exact_baseline_errors():
    data = prepared()
    lengths = []

    def spy(context, steps):
        lengths.append(len(context))
        assert context == data.values[:len(context)]
        return persistence(context, steps)

    results = backtest(data, spy)
    assert lengths == [45, 52, 59]
    assert [r.n_evaluations for r in results] == [3, 3, 3]
    assert [r.mae for r in results] == [Decimal("0.001"), Decimal("0.003"), Decimal("0.007")]
    assert all(r.rmse == r.mae for r in results)
    assert rolling_mean(data.values, 7)[0] == sum(data.values[-7:]) / 7


def test_need_multiple_windows():
    with pytest.raises(SeriesUnavailable, match="NEED_3"):
        backtest(prepared(series_payload(65)))


def test_unrun_round_trip_remains_unavailable(tmp_path):
    bundle = ForecastBundle.model_validate(bundle_payload())
    path = export_bundle(tmp_path, bundle)
    assert ForecastBundle.model_validate_json(path.read_text()) == bundle
    assert ForecastCache(tmp_path).load(strategy(), horizon_days=1, now=NOW).reason == "FORECAST_NOT_VALIDATED"


def test_missing_and_normal_no_io(tmp_path, monkeypatch):
    cache = ForecastCache(tmp_path)
    assert cache.load(strategy(), horizon_days=1, now=NOW).reason == "FORECAST_UNAVAILABLE"
    monkeypatch.setattr(cache, "load", lambda *a, **kw: pytest.fail("Normal read the cache"))
    assert cache.for_mode("normal", strategy(), horizon_days=7, now=NOW).bundle is None


@pytest.mark.parametrize("raw", ["{invalid json", '{"status": "VALIDATED"}', '{"chain_id": 8453}'])
def test_corrupt_cache_falls_back(tmp_path, raw):
    path = cache_path(tmp_path, strategy().strategy_id)
    path.parent.mkdir(parents=True)
    path.write_text(raw)
    assert ForecastCache(tmp_path).load(strategy(), horizon_days=1, now=NOW).reason == "FORECAST_NOT_VALIDATED"


@pytest.mark.parametrize("horizon", [1, 3, 7])
def test_contract_gate_all_horizons(tmp_path, horizon):
    export_bundle(tmp_path, ForecastBundle.model_validate(validated_fixture()))
    assert ForecastCache(tmp_path).load(strategy(True), horizon_days=horizon, now=NOW).bundle is not None
    assert ForecastCache(tmp_path).load(strategy(), horizon_days=horizon, now=NOW).bundle is None


def test_stale_from_observation_not_generation(tmp_path):
    payload = validated_fixture()
    payload.update(latest_observation_at=NOW-timedelta(days=2), training_cutoff_at=NOW-timedelta(days=2))
    export_bundle(tmp_path, ForecastBundle.model_validate(payload))
    assert ForecastCache(tmp_path).load(strategy(True), horizon_days=1, now=NOW).reason == "FORECAST_STALE"


@pytest.mark.parametrize("mutation", ["quantiles", "no_benchmark", "no_gain", "wrong_market", "future", "wrong_chain"])
def test_reject_bad_validation(tmp_path, mutation):
    payload = validated_fixture()
    if mutation == "quantiles":
        payload["points"][0]["q10"] = "2"
    elif mutation == "no_benchmark":
        payload["backtest"]["n_evaluations"] = 1
    elif mutation == "no_gain":
        payload["backtest"]["mae_model"] = "0.02"
    elif mutation == "wrong_market":
        payload["entity_id"] = f"pendle:1:{PT}"
    elif mutation == "future":
        payload["generated_at"] = NOW + timedelta(days=1)
    elif mutation == "wrong_chain":
        payload["chain_id"] = 8453
    path = cache_path(tmp_path, strategy().strategy_id)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload, default=str))
    assert ForecastCache(tmp_path).load(strategy(True), horizon_days=7, now=NOW).reason == "FORECAST_NOT_VALIDATED"


def test_safe_paths_and_invalid_export_preserve_old_file(tmp_path):
    assert cache_path(tmp_path, "../../escape").is_relative_to(tmp_path)
    assert cache_path(tmp_path, "a:b") != cache_path(tmp_path, "a/b")
    bundle = ForecastBundle.model_validate(bundle_payload())
    path = export_bundle(tmp_path, bundle)
    before = path.read_bytes()
    with pytest.raises(ValidationError):
        export_bundle(tmp_path, bundle.model_copy(update={"status": "VALIDATED"}))
    assert path.read_bytes() == before
