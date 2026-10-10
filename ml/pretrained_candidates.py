"""Pinned alternative pretrained forecasters for offline research only."""
import re
from importlib.metadata import version

from .chronos2 import Chronos2Forecaster

MODEL_IDS = {
    'chronos2': 'amazon/chronos-2',
    'bolt': 'amazon/chronos-bolt-base',
    'timesfm': 'google/timesfm-2.5-200m-pytorch',
}
TIMESFM_CONFIG = dict(max_context=256, max_horizon=128, normalize_inputs=True,
    use_continuous_quantile_head=True, force_flip_invariance=True,
    infer_is_positive=True, fix_quantile_crossing=True)


class TimesFMPipeline:
    """Convert official TimesFM deciles to the existing strict forecast contract."""
    def __init__(self, model):
        self.model = model

    def predict_df(self, frame, *, prediction_length, quantile_levels,
                   id_column, timestamp_column, target):
        import numpy as np
        import pandas as pd
        if frame[id_column].nunique() != 1 or quantile_levels != [0.1, 0.5, 0.9]:
            raise ValueError('adapter supports one series and q10/q50/q90 only')
        _, quantiles = self.model.forecast(horizon=prediction_length,
            inputs=[frame[target].to_numpy(dtype=np.float32)])
        if quantiles.shape != (1, prediction_length, 10):
            raise ValueError('unexpected TimesFM decile shape')
        # Official layout: point output, q10, q20, ..., q90. Median is index 5.
        return pd.DataFrame({id_column: frame[id_column].iloc[0],
            timestamp_column: pd.date_range(frame[timestamp_column].iloc[-1] + pd.Timedelta(days=1),
                                            periods=prediction_length, freq='D'),
            'target_name': target, '0.1': quantiles[0,:,1],
            '0.5': quantiles[0,:,5], '0.9': quantiles[0,:,9]})


class BoltPipeline:
    """Clamp outer quantiles to median; never change the point forecast."""
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.corrected_rows = 0

    def predict_df(self, frame, **kwargs):
        result = self.pipeline.predict_df(frame, **kwargs).copy()
        crossed = (result['0.1'] > result['0.5']) | (result['0.9'] < result['0.5'])
        self.corrected_rows += int(crossed.sum())
        result['0.1'] = result[['0.1', '0.5']].min(axis=1, skipna=False)
        result['0.9'] = result[['0.9', '0.5']].max(axis=1, skipna=False)
        return result


def load_candidate(name, *, revision):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('revision must be a model commit SHA')
    if name == 'chronos2':
        return Chronos2Forecaster.load(revision=revision, device='cpu', seed=42)
    import torch
    torch.manual_seed(42)
    model_id = MODEL_IDS[name]
    if name == 'bolt':
        from chronos import ChronosBoltPipeline
        pipeline = BoltPipeline(ChronosBoltPipeline.from_pretrained(model_id, revision=revision, device_map='cpu'))
        package = 'chronos-forecasting'
    else:
        import timesfm
        model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(model_id,
            revision=revision, torch_compile=False)
        if model.model.device.type != 'cpu':
            raise RuntimeError('research protocol requires CPU')
        model.compile(timesfm.ForecastConfig(**TIMESFM_CONFIG))
        pipeline = TimesFMPipeline(model)
        package = 'timesfm'
    return Chronos2Forecaster(pipeline, model_id=model_id,
        model_version=f'{package}={version(package)};model={revision}')
