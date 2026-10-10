"""Expanding-origin daily benchmark; no Chronos inference or data download."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from .series import DailySeries, SeriesUnavailable

HORIZONS = (1, 3, 7)
Predictor = Callable[[tuple[Decimal, ...], int], tuple[Decimal, ...]]


def persistence(context: tuple[Decimal, ...], steps: int) -> tuple[Decimal, ...]:
    return (context[-1],) * steps


def rolling_mean(context: tuple[Decimal, ...], steps: int) -> tuple[Decimal, ...]:
    window = context[-7:]
    return (sum(window) / len(window),) * steps


@dataclass(frozen=True)
class HorizonErrors:
    horizon_days: int
    n_evaluations: int
    mae: Decimal
    rmse: Decimal
    directional_accuracy: Decimal


def backtest(
    series: DailySeries, predictor: Predictor = persistence, *, min_context: int = 45,
    exploratory: bool = False,
) -> tuple[HorizonErrors, ...]:
    """Non-overlapping seven-day test windows with expanding past-only context.

    Exactly the same origin schedule is used for any predictor. No target or
    future covariate is passed to it. Real point-in-time provenance still needs
    auditing before model validation; a retrospective API export alone cannot
    establish absence of revisions to past prices.
    """
    if min_context < (14 if exploratory else 45):
        raise ValueError("minimum context cannot be below 45 days")
    origins = tuple(range(min_context, len(series.values) - 6, 7))
    if len(origins) < (1 if exploratory else 3):
        raise SeriesUnavailable("NEED_3_BACKTEST_WINDOWS: at least 66 daily observations")
    errors = {h: [] for h in HORIZONS}
    directions = {h: [] for h in HORIZONS}
    for origin in origins:
        context = series.values[:origin]
        predictions = predictor(context, 7)
        if len(predictions) != 7 or any(not p.is_finite() or p <= 0 for p in predictions):
            raise ValueError("predictor must return seven finite positive daily prices")
        for h in HORIZONS:
            actual = series.values[origin + h - 1]
            predicted = predictions[h - 1]
            errors[h].append(predicted - actual)
            sign = lambda x: (x > 0) - (x < 0)
            directions[h].append(sign(predicted - context[-1]) == sign(actual - context[-1]))
    return tuple(
        HorizonErrors(
            h, len(errors[h]),
            sum(abs(e) for e in errors[h]) / len(errors[h]),
            (sum(e * e for e in errors[h]) / len(errors[h])).sqrt(),
            Decimal(sum(directions[h])) / len(directions[h]),
        ) for h in HORIZONS
    )
