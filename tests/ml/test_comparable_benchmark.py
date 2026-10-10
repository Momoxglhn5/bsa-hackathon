"""Synthetic final-test checks: never fetch data or load model weights."""
import pytest

from ml.chronos2 import Chronos2Forecaster
from ml.run_comparable_benchmark import final_test
from test_chronos2 import SyntheticPipeline
from test_foundations import prepared, series_payload


def test_reserved_weeks_use_only_preceding_observations_and_matching_baselines():
    data = prepared(series_payload(80))
    pipeline = SyntheticPipeline()
    result = final_test(data, Chronos2Forecaster(pipeline,model_version='SYNTHETIC'))
    assert [len(frame) for frame,_ in pipeline.calls] == [66,73]
    assert result['n_origins'] == 2
    assert len(result['windows']) == 6
    assert {r['timestamp'] for r in result['windows']} == {
        data.timestamps[i].isoformat() for i in (66,68,72,73,75,79)}
    for frame,_ in pipeline.calls:
        assert list(frame.target) == [float(v) for v in data.values[:len(frame)]]
    for row in result['windows']:
        assert row['cutoff_at'] < row['timestamp']
        assert row['q50'] == row['naive'] or float(row['q50']) == float(row['naive'])
    for metric in result['horizon_metrics']:
        assert float(metric['mae_model']) == pytest.approx(float(metric['mae_naive']))
        assert metric['n_evaluations'] == 2


def test_final_test_cannot_consume_development_below_standard_requirement():
    with pytest.raises(ValueError,match='66 development'):
        final_test(prepared(series_payload(79)),None)
