"""Pure oracle Monte Carlo metrics, with losses in loan-token units.

These are mark-to-model losses, not realized liquidation losses or USD values.
"""
from decimal import Decimal
from numbers import Integral
from typing import Any

import numpy as np


def _finite_decimal(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise ValueError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    return value


def _finite_scalar(value: Any, name: str) -> float:
    if isinstance(value, (bool, str, bytes)):
        raise ValueError(f"{name} must be a numeric scalar")
    try:
        array = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite numeric scalar") from exc
    if array.ndim != 0 or not np.isfinite(array).item():
        raise ValueError(f"{name} must be a finite numeric scalar")
    return float(array)


def _finite_vector(value: np.ndarray, name: str) -> np.ndarray:
    if not isinstance(value, np.ndarray):
        raise ValueError(f"{name} must be a NumPy ndarray")
    if value.ndim != 1 or value.size == 0:
        raise ValueError(f"{name} must be a non-empty one-dimensional array")
    if value.dtype.kind not in "iuf":
        raise ValueError(f"{name} must contain real numeric values")
    with np.errstate(over="ignore", invalid="ignore"):
        array = value.astype(np.float64, copy=False)
    if not np.isfinite(array).all():
        raise ValueError(f"{name} must contain only finite float64 values")
    return array


def critical_oracle_drop_ratio(
    collateral_value: Decimal,
    debt_value: Decimal,
    lltv: Decimal,
) -> Decimal:
    """Return 1 - debt / (LLTV * collateral), without clipping.

    Decimal arithmetic only; repeating divisions use the active Decimal
    context's precision. Zero/negative means the position is at/above LLTV.
    """
    collateral_value = _finite_decimal(collateral_value, "collateral_value")
    debt_value = _finite_decimal(debt_value, "debt_value")
    lltv = _finite_decimal(lltv, "lltv")
    if collateral_value <= 0:
        raise ValueError("collateral_value must be greater than zero")
    if lltv <= 0:
        raise ValueError("lltv must be greater than zero")
    if debt_value < 0:
        raise ValueError("debt_value must be non-negative")
    denominator = lltv * collateral_value
    if not denominator.is_finite() or denominator <= 0:
        raise ValueError("lltv * collateral_value must be finite and positive")
    result = Decimal("1") - debt_value / denominator
    if not result.is_finite():
        raise ValueError("critical oracle drop ratio must be finite")
    return result


def wilson_ci_95(successes: int, total: int) -> tuple[float, float]:
    """Wilson interval for sampling frequency, not real-world probability."""
    if (
        isinstance(successes, bool)
        or isinstance(total, bool)
        or not isinstance(successes, Integral)
        or not isinstance(total, Integral)
    ):
        raise ValueError("successes and total must be integers")
    successes, total = int(successes), int(total)
    if total <= 0 or not 0 <= successes <= total:
        raise ValueError("Require total > 0 and 0 <= successes <= total")
    z = 1.96
    z_squared = z * z
    proportion = successes / total
    inverse_total = 1 / total
    denominator = 1 + z_squared * inverse_total
    center = (proportion + z_squared * inverse_total / 2) / denominator
    half_width = (
        z * np.sqrt(
            proportion * (1 - proportion) * inverse_total
            + z_squared * inverse_total**2 / 4
        ) / denominator
    )
    bounds = np.asarray([center - half_width, center + half_width], dtype=np.float64)
    if not np.isfinite(bounds).all():
        raise ValueError("Wilson interval bounds must be finite")
    # Bound only floating-point roundoff at probability endpoints.
    return max(0.0, float(bounds[0])), min(1.0, float(bounds[1]))


def terminal_ltv_quantiles(terminal_ltv: np.ndarray) -> tuple[float, float]:
    """Return terminal LTV p50 and p95 with linear interpolation."""
    values = _finite_vector(terminal_ltv, "terminal_ltv")
    if np.any(values < 0):
        raise ValueError("terminal_ltv must be non-negative")
    with np.errstate(over="ignore", invalid="ignore"):
        quantiles = np.quantile(values, [0.50, 0.95], method="linear")
    if not np.isfinite(quantiles).all():
        raise ValueError("Terminal LTV quantiles must be finite")
    return float(quantiles[0]), float(quantiles[1])


def min_health_factor_p05(min_health_factors: np.ndarray) -> float:
    """Return p05 of the per-path minimum health factors."""
    values = _finite_vector(min_health_factors, "min_health_factors")
    if np.any(values < 0):
        raise ValueError("min_health_factors must be non-negative")
    with np.errstate(over="ignore", invalid="ignore"):
        result = np.quantile(values, 0.05, method="linear")
    if not np.isfinite(result):
        raise ValueError("Health factor p05 must be finite")
    return float(result)


def mark_to_model_losses(
    initial_collateral: float,
    initial_debt: float,
    terminal_collateral: np.ndarray,
    terminal_debt: np.ndarray,
) -> np.ndarray:
    """Return max(initial equity - terminal equity, 0) per path.

    Inputs use the same loan-token denomination. Terminal arrays must have
    identical shapes; broadcasting is rejected.
    """
    initial_collateral = _finite_scalar(initial_collateral, "initial_collateral")
    initial_debt = _finite_scalar(initial_debt, "initial_debt")
    collateral = _finite_vector(terminal_collateral, "terminal_collateral")
    debt = _finite_vector(terminal_debt, "terminal_debt")
    if collateral.shape != debt.shape:
        raise ValueError("Terminal collateral and debt must have identical shapes")
    if initial_collateral < 0 or initial_debt < 0:
        raise ValueError("Initial collateral and debt must be non-negative")
    if np.any(collateral < 0) or np.any(debt < 0):
        raise ValueError("Terminal collateral and debt must be non-negative")
    with np.errstate(over="ignore", invalid="ignore"):
        initial_equity = np.float64(initial_collateral) - np.float64(initial_debt)
        terminal_equity = collateral - debt
        equity_decline = initial_equity - terminal_equity
    if (
        not np.isfinite(initial_equity)
        or not np.isfinite(terminal_equity).all()
        or not np.isfinite(equity_decline).all()
    ):
        raise ValueError("Equity calculations exceed finite float64 range")
    return np.maximum(equity_decline, 0.0)


def var_es_95(losses: np.ndarray) -> tuple[float, float]:
    """Return VaR95 and mean(losses >= VaR95), including ties, in loan units."""
    values = _finite_vector(losses, "losses")
    if np.any(values < 0):
        raise ValueError("losses must be non-negative")
    with np.errstate(over="ignore", invalid="ignore"):
        var95 = np.quantile(values, 0.95, method="linear")
    if not np.isfinite(var95):
        raise ValueError("VaR95 must be finite")
    tail = values[values >= var95]
    if tail.size == 0:
        raise ValueError("Cannot compute ES95 from an empty tail")
    # Scale before averaging to avoid overflow in the sum of finite losses.
    scale = float(np.max(tail))
    es95 = 0.0 if scale == 0 else scale * float(np.mean(tail / scale, dtype=np.float64))
    if not np.isfinite(es95):
        raise ValueError("ES95 must be finite")
    return float(var95), float(es95)
