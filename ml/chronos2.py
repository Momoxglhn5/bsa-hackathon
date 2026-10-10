"""Offline Chronos-2 integration. Importing this module never loads a model."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from importlib.metadata import version
import math
from time import perf_counter
from typing import Any

from .series import DailySeries, SeriesUnavailable

MODEL_ID = "amazon/chronos-2"


@dataclass(frozen=True)
class QuantileDay:
    timestamp: datetime
    q10: Decimal
    q50: Decimal
    q90: Decimal


@dataclass(frozen=True)
class Prediction:
    days: tuple[QuantileDay, ...]
    inference_seconds: float


class Chronos2Forecaster:
    def __init__(self, pipeline: Any, *, model_version: str):
        """Pipeline injection allows protocol tests without torch or weights."""
        self.pipeline = pipeline
        self.model_version = model_version

    @classmethod
    def load(cls, *, revision: str, device: str = "auto", seed: int = 42):
        """Explicit Colab-only load; pin an immutable Hugging Face model commit."""
        import re
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("revision must be a 40-character model commit SHA")
        if device not in ("auto", "cuda", "cpu"):
            raise ValueError("device must be auto, cuda or cpu")
        import torch
        from chronos import Chronos2Pipeline

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable; select CPU explicitly")
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        pipeline = Chronos2Pipeline.from_pretrained(
            MODEL_ID, revision=revision, device_map=device)
        return cls(pipeline, model_version=f"chronos-forecasting={version('chronos-forecasting')};model={revision}")

    def predict(self, series: DailySeries, *, context_length: int | None = None) -> Prediction:
        """Forecast seven daily quantile points from a past-only prefix."""
        import pandas as pd

        n = len(series.values) if context_length is None else context_length
        if n < 45 or n > len(series.values):
            raise SeriesUnavailable("INVALID_CONTEXT_LENGTH")
        timestamps = series.timestamps[:n]
        values = series.values[:n]
        if len(timestamps) != n or any(b-a != timedelta(days=1) for a, b in zip(timestamps, timestamps[1:])):
            raise SeriesUnavailable("IRREGULAR_CONTEXT")
        if timestamps[-1] + timedelta(days=7) >= series.expiry:
            raise SeriesUnavailable("HORIZON_REACHES_PT_EXPIRY")
        numeric = [float(v) for v in values]
        if any(not math.isfinite(v) or v <= 0 for v in numeric):
            raise SeriesUnavailable("PRICE_NOT_REPRESENTABLE_FOR_MODEL")
        # Naive timestamps inside pandas explicitly mean UTC, matching Chronos API.
        utc = pd.to_datetime(timestamps, utc=True).tz_localize(None)
        frame = pd.DataFrame({"item_id": series.entity_id, "timestamp": utc, "target": numeric})
        started = perf_counter()
        output = self.pipeline.predict_df(
            frame, prediction_length=7, quantile_levels=[0.1, 0.5, 0.9],
            id_column="item_id", timestamp_column="timestamp", target="target",
        )
        elapsed = perf_counter() - started
        required = {"item_id", "timestamp", "0.1", "0.5", "0.9"}
        if not isinstance(output, pd.DataFrame) or not required.issubset(output.columns):
            raise ValueError("unexpected Chronos output schema; inspect package version")
        if len(output) != 7 or not (output["item_id"] == series.entity_id).all():
            raise ValueError("Chronos returned wrong series or number of days")
        if "target_name" in output and not (output["target_name"] == "target").all():
            raise ValueError("Chronos returned another target")
        output = output.copy()
        output["timestamp"] = pd.to_datetime(output["timestamp"], utc=True)
        output = output.sort_values("timestamp")
        days = []
        for h, row in enumerate(output.to_dict("records"), start=1):
            timestamp = row["timestamp"].to_pydatetime()
            if timestamp != timestamps[-1].astimezone(timezone.utc) + timedelta(days=h):
                raise ValueError("Chronos timestamps are not the next seven UTC days")
            quantiles = tuple(Decimal(str(row[k])) for k in ("0.1", "0.5", "0.9"))
            if any(not q.is_finite() or q <= 0 for q in quantiles):
                raise ValueError("Chronos returned non-finite or nonpositive prices")
            if not quantiles[0] <= quantiles[1] <= quantiles[2]:
                raise ValueError("Chronos returned unordered quantiles")
            days.append(QuantileDay(timestamp, *quantiles))
        return Prediction(tuple(days), elapsed)


def evaluate(series: DailySeries, model: Chronos2Forecaster) -> dict:
    """Research report only. Never assigns VALIDATED or writes the runtime cache.

    Source availability/revision audit and human review of per-horizon scores
    are still required before creating a validated ForecastBundle.
    """
    from .benchmark import HORIZONS, backtest, persistence, rolling_mean

    n = len(series.values)
    origins = tuple(range(45, n - 6, 7))
    if len(origins) < 3:
        raise SeriesUnavailable("NEED_3_BACKTEST_WINDOWS")
    records = []
    for origin in origins:
        prediction = model.predict(series, context_length=origin)
        for h in HORIZONS:
            point = prediction.days[h-1]
            actual = series.values[origin+h-1]
            error = point.q50-actual
            losses = []
            for level, q in zip((Decimal("0.1"), Decimal("0.5"), Decimal("0.9")),
                                (point.q10, point.q50, point.q90)):
                residual = actual-q
                losses.append(max(level*residual, (level-1)*residual))
            last = series.values[origin-1]
            sign = lambda x: (x > 0) - (x < 0)
            records.append(dict(
                cutoff_at=series.timestamps[origin-1].isoformat(), horizon_days=h,
                timestamp=point.timestamp.isoformat(), actual=str(actual),
                q10=str(point.q10), q50=str(point.q50), q90=str(point.q90),
                error=error, pinball=sum(losses)/3,
                covered=point.q10 <= actual <= point.q90,
                direction_correct=sign(point.q50-last) == sign(actual-last),
                inference_seconds=prediction.inference_seconds,
            ))
    naive = backtest(series, persistence)
    mean = backtest(series, rolling_mean)
    summaries = []
    for h, baseline, moving in zip(HORIZONS, naive, mean):
        rows = [r for r in records if r["horizon_days"] == h]
        count = len(rows)
        mae = sum(abs(r["error"]) for r in rows)/count
        summaries.append(dict(
            horizon_days=h, n_evaluations=count, mae_model=str(mae),
            rmse_model=str((sum(r["error"]**2 for r in rows)/count).sqrt()),
            mae_naive=str(baseline.mae), mae_rolling_mean=str(moving.mae),
            pinball_loss=str(sum(r["pinball"] for r in rows)/count),
            coverage_q10_q90=str(Decimal(sum(r["covered"] for r in rows))/count),
            directional_accuracy=str(Decimal(sum(r["direction_correct"] for r in rows))/count),
            beats_naive=mae < baseline.mae,
        ))
    latest = model.predict(series)
    for row in records:
        row["error"] = str(row["error"])
        row["pinball"] = str(row["pinball"])
    return dict(
        report_type="marilou-research-report-v1", status="RESEARCH_ONLY",
        model_id=MODEL_ID, model_version=model.model_version, zero_shot=True,
        data_mode=series.data_mode.value, strategy_id=series.strategy_id,
        entity_id=series.entity_id, source_snapshot_ref=series.source_snapshot_ref,
        source_as_of=series.source_as_of.isoformat(), retrieved_at=series.retrieved_at.isoformat(),
        generated_at=datetime.now(timezone.utc).isoformat(),
        training_cutoff_at=series.timestamps[-1].isoformat(),
        n_origins=len(origins), horizon_metrics=summaries, windows=records,
        forecast=[dict(horizon_days=h, timestamp=latest.days[h-1].timestamp.isoformat(),
                       q10=str(latest.days[h-1].q10), q50=str(latest.days[h-1].q50),
                       q90=str(latest.days[h-1].q90)) for h in HORIZONS],
        final_inference_seconds=latest.inference_seconds,
        limitations=["Research report, not an accepted ForecastBundle",
                     "Historical availability/revisions require audit",
                     "No hack probabilities or Morpho oracle predictions"],
    )
