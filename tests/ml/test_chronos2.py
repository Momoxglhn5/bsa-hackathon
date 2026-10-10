"""Adapter tests with SYNTHETIC pipeline output; no actual Chronos model."""
from datetime import timedelta
from decimal import Decimal

import pandas as pd
import pytest

from ml.chronos2 import Chronos2Forecaster, evaluate
from test_foundations import prepared


class SyntheticPipeline:
    def __init__(self, mutation=None):
        self.calls = []
        self.mutation = mutation

    def predict_df(self, frame, **kwargs):
        self.calls.append((frame.copy(), kwargs))
        cutoff = frame.timestamp.iloc[-1]
        last = frame.target.iloc[-1]
        rows = [dict(item_id=frame.item_id.iloc[0],
                     timestamp=cutoff+timedelta(days=h), target_name="target",
                     **{"0.1": last-0.01, "0.5": last, "0.9": last+0.01})
                for h in range(1, 8)]
        if self.mutation == "unordered":
            rows[0]["0.1"] = last+1
        elif self.mutation == "nan":
            rows[0]["0.5"] = float("nan")
        elif self.mutation == "negative":
            rows[0]["0.1"] = -1
        elif self.mutation == "wrong_day":
            rows[0]["timestamp"] = cutoff
        elif self.mutation == "wrong_entity":
            rows[0]["item_id"] = "wrong"
        elif self.mutation == "wrong_target":
            rows[0]["target_name"] = "oracle"
        elif self.mutation == "short":
            rows.pop()
        frame = pd.DataFrame(rows)
        if self.mutation == "missing_column":
            del frame["0.5"]
        return frame


def test_exact_context_quantiles_and_daily_timestamps():
    series = prepared()
    pipeline = SyntheticPipeline()
    model = Chronos2Forecaster(pipeline, model_version="SYNTHETIC")
    result = model.predict(series, context_length=45)
    frame, kwargs = pipeline.calls[0]
    assert len(frame) == 45
    assert list(frame.columns) == ["item_id", "timestamp", "target"]
    assert frame.timestamp.iloc[-1].tz_localize("UTC").to_pydatetime() == series.timestamps[44]
    assert kwargs["prediction_length"] == 7
    assert kwargs["quantile_levels"] == [0.1, 0.5, 0.9]
    assert "future_df" not in kwargs
    assert result.days[0].timestamp == series.timestamps[44]+timedelta(days=1)
    assert result.days[6].timestamp == series.timestamps[44]+timedelta(days=7)
    assert isinstance(result.days[0].q50, Decimal)


@pytest.mark.parametrize("mutation", ["unordered", "nan", "negative", "wrong_day",
                                      "wrong_entity", "wrong_target", "short", "missing_column"])
def test_bad_model_output_is_rejected(mutation):
    model = Chronos2Forecaster(SyntheticPipeline(mutation), model_version="SYNTHETIC")
    with pytest.raises(ValueError):
        model.predict(prepared())


def test_research_report_cannot_validate_a_model():
    pipeline = SyntheticPipeline()
    report = evaluate(prepared(), Chronos2Forecaster(pipeline, model_version="SYNTHETIC"))
    assert [len(frame) for frame, _ in pipeline.calls] == [45, 52, 59, 66]
    assert report["status"] == "RESEARCH_ONLY"
    assert report["data_mode"] == "DEMO"
    assert report["n_origins"] == 3
    assert len(report["windows"]) == 9
    assert [r["horizon_days"] for r in report["forecast"]] == [1, 3, 7]
    for metric in report["horizon_metrics"]:
        assert Decimal(metric["mae_model"]) == Decimal(metric["mae_naive"])
        assert not metric["beats_naive"]
        assert 0 <= Decimal(metric["coverage_q10_q90"]) <= 1
        assert Decimal(metric["pinball_loss"]) >= 0


def test_model_revision_checked_before_optional_imports():
    with pytest.raises(ValueError, match="commit SHA"):
        Chronos2Forecaster.load(revision="main")


def test_short_research_keeps_default_gate_and_past_only_windows():
    from dataclasses import replace
    from ml.series import SeriesUnavailable
    full = prepared()
    short = replace(full, timestamps=full.timestamps[:31], values=full.values[:31])
    pipeline = SyntheticPipeline()
    model = Chronos2Forecaster(pipeline, model_version="SYNTHETIC")
    with pytest.raises(SeriesUnavailable):
        model.predict(short)
    with pytest.raises(SeriesUnavailable):
        evaluate(short, model)
    report = evaluate(short, model, exploratory=True)
    assert [len(frame) for frame, _ in pipeline.calls] == [17, 24, 31]
    assert report['status'] == 'RESEARCH_ONLY'
    assert report['exploratory'] is True
    assert report['n_origins'] == 2
    for row in report['windows']:
        assert row['cutoff_at'] < row['timestamp']
