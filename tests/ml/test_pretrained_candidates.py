import numpy as np
import pandas as pd
import pytest

from ml.pretrained_candidates import TimesFMPipeline, load_candidate


class FakeTimesFM:
    def forecast(self, horizon, inputs):
        self.inputs = inputs
        quantiles = np.broadcast_to(np.arange(10), (1, horizon, 10))
        return quantiles[:,:,5], quantiles


def test_timesfm_maps_deciles_and_next_days_without_changing_context():
    model = FakeTimesFM()
    frame = pd.DataFrame({'item_id': ['pt'] * 3,
        'timestamp': pd.date_range('2026-01-01', periods=3), 'target': [1., 2., 3.]})
    result = TimesFMPipeline(model).predict_df(frame, prediction_length=7,
        quantile_levels=[.1,.5,.9], id_column='item_id', timestamp_column='timestamp', target='target')
    np.testing.assert_array_equal(model.inputs[0], [1.,2.,3.])
    assert list(result['0.1']) == [1]*7
    assert list(result['0.5']) == [5]*7
    assert list(result['0.9']) == [9]*7
    assert result['timestamp'].iloc[0] == pd.Timestamp('2026-01-04')
    assert result['timestamp'].iloc[-1] == pd.Timestamp('2026-01-10')
    assert (result['item_id'] == 'pt').all()


def test_candidate_requires_immutable_revision_before_loading():
    with pytest.raises(ValueError, match='commit SHA'):
        load_candidate('timesfm', revision='main')


def test_research_report_identifies_injected_alternative():
    from ml.chronos2 import Chronos2Forecaster, evaluate
    from test_chronos2 import SyntheticPipeline
    from test_foundations import prepared
    report = evaluate(prepared(), Chronos2Forecaster(SyntheticPipeline(),
        model_id='alternative/model', model_version='SYNTHETIC'))
    assert report['model_id'] == 'alternative/model'
    assert report['status'] == 'RESEARCH_ONLY'


def test_bolt_outer_quantile_correction_preserves_median_and_nan():
    from ml.pretrained_candidates import BoltPipeline
    class Crossed:
        def predict_df(self, frame, **kwargs):
            return pd.DataFrame({'0.1':[2.,float('nan')], '0.5':[1.,1.], '0.9':[.5,2.]})
    pipeline = BoltPipeline(Crossed())
    result = pipeline.predict_df(None)
    assert result['0.5'].tolist() == [1.,1.]
    assert result['0.1'].iloc[0] == result['0.9'].iloc[0] == 1.
    assert np.isnan(result['0.1'].iloc[1])
    assert pipeline.corrected_rows == 1
