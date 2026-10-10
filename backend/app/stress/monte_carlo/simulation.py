"""Vectorized PR1 GBM simulations of oracle prices, never PT spot prices.

Threshold breach frequencies are model-implied simulation frequencies, not
objective liquidation probabilities. Losses are mark-to-model loan-token units.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from decimal import Decimal, DecimalException
from numbers import Integral
from typing import Callable

import numpy as np

from . import metrics
from .models import (
    MODEL_VERSION,
    DebtModel,
    InputScope,
    McStatus,
    MonteCarloData,
    MonteCarloProvenance,
    MonteCarloRequest,
    MonteCarloResult,
    MonteCarloSamplingCI,
    PositionSide,
)

_NUMERIC_ERRORS = (ValueError, OverflowError, FloatingPointError, DecimalException)


def _integer(value: int, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


def _float(value: float | Decimal, name: str) -> float:
    if isinstance(value, (bool, str, bytes)) or not np.isscalar(value):
        raise ValueError(f"{name} must be a finite numeric scalar")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} cannot be represented as float64") from exc
    if not np.isfinite(result) or (result == 0 and value != 0):
        raise ValueError(f"{name} is outside the usable float64 range")
    return result


def _finite(values: np.ndarray, name: str, *, positive: bool = False) -> None:
    if not np.isfinite(values).all() or (positive and np.any(values <= 0)):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))


def _decimal(value: float) -> Decimal:
    return Decimal(repr(_float(value, "metric")))


def simulate_oracle_paths(
    p0: float,
    sigma: float,
    mu: float,
    horizon_days: int,
    num_paths: int,
    seed: int,
) -> np.ndarray:
    """Return positive float64 prices with shape (num_paths, H + 1).

    The initial column is exactly p0. An unrepresentable path raises ValueError;
    no paths are discarded, clipped or replaced.
    """
    p0, sigma, mu = _float(p0, "p0"), _float(sigma, "sigma"), _float(mu, "mu")
    if p0 <= 0 or sigma < 0:
        raise ValueError("Require p0 > 0 and sigma >= 0")
    horizon_days = _integer(horizon_days, "horizon_days", 0)
    num_paths = _integer(num_paths, "num_paths", 1)
    seed = _integer(seed, "seed", 0)
    rng = np.random.default_rng(seed)
    dt = 1.0 / 365.0
    z = rng.standard_normal((num_paths, horizon_days))
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        log_returns = (
            (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z
        )
        paths = np.empty((num_paths, horizon_days + 1), dtype=np.float64)
        paths[:, 0] = p0
        paths[:, 1:] = p0 * np.exp(np.cumsum(log_returns, axis=1))
    _finite(paths, "oracle paths", positive=True)
    return paths


def debt_path(
    d0: float | Decimal,
    horizon_days: int,
    debt_model: DebtModel,
    apr: float | Decimal | None = None,
) -> np.ndarray:
    """Return debt at days 0..H; CONSTANT_APR uses continuous compounding."""
    d0 = _float(d0, "d0")
    if d0 < 0:
        raise ValueError("d0 must be non-negative")
    horizon_days = _integer(horizon_days, "horizon_days", 0)
    debt_model = DebtModel(debt_model)
    if debt_model == DebtModel.FROZEN_DEBT:
        return np.full(horizon_days + 1, d0, dtype=np.float64)
    if apr is None:
        raise ValueError("CONSTANT_APR requires apr")
    apr = _float(apr, "apr")
    if d0 == 0:
        return np.zeros(horizon_days + 1, dtype=np.float64)
    t = np.arange(horizon_days + 1, dtype=np.float64)
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        debt = d0 * np.exp(apr * t / 365.0)
    debt[0] = d0
    _finite(debt, "debt path", positive=True)
    return debt


def run_monte_carlo(request: MonteCarloRequest) -> MonteCarloResult:
    """Run a hypothetical oracle-price simulation without live dependencies.

    Decimal initial metrics use the active Decimal context, with no float
    conversion. Numerical failures leave affected metrics unset and explicitly
    return PARTIAL; they never produce fabricated zeros or non-finite JSON.
    """
    if not isinstance(request, MonteCarloRequest):
        raise TypeError("request must be a MonteCarloRequest")
    warnings = [
        "MODEL_RISK_GBM",
        "NOT_A_REAL_WORLD_LIQUIDATION_PROBABILITY",
        "DAILY_STEPS_MAY_MISS_INTRADAY_BREACH",
    ]
    if request.input_scope == InputScope.HYPOTHETICAL:
        warnings.append("SYNTHETIC_FIXTURE_NOT_LIVE")
    if request.simulation.debt_model == DebtModel.FROZEN_DEBT:
        warnings.append("FROZEN_DEBT")
    warnings.extend(request.warnings)
    missing: list[str] = []
    provenance = MonteCarloProvenance(
        calibration=request.simulation.source_mode,
        evidence_ids=list(request.evidence_ids),
    )

    def result(status: McStatus, data: MonteCarloData | None) -> MonteCarloResult:
        return MonteCarloResult(
            status=status, as_of=request.snapshot_as_of, data=data,
            provenance=provenance, missing_fields=list(dict.fromkeys(missing)),
            warnings=list(dict.fromkeys(warnings)),
        )

    if request.position_side == PositionSide.SUPPLY:
        warnings.append("SUPPLY_POSITION_NO_BORROWER_LIQUIDATION")
        return result(McStatus.NOT_APPLICABLE, None)
    if request.debt_loan_units == 0:
        warnings.append("NO_DEBT")
        return result(McStatus.NOT_APPLICABLE, None)
    if request.pt_expiry is not None:
        try:
            horizon_end = request.snapshot_as_of.astimezone(timezone.utc) + timedelta(
                days=request.horizon_days
            )
        except OverflowError:
            missing.append("valid_horizon")
            warnings.append("HORIZON_DATETIME_OUT_OF_RANGE")
            return result(McStatus.UNAVAILABLE, None)
        if request.pt_expiry.astimezone(timezone.utc) <= horizon_end:
            missing.append("valid_horizon")
            warnings.append("PT_EXPIRES_WITHIN_HORIZON")
            return result(McStatus.UNAVAILABLE, None)

    data = MonteCarloData(
        strategy_id=request.strategy_id, position_id=request.position_id,
        model_version=MODEL_VERSION, horizon_days=request.horizon_days,
        assumptions=[
            "Oracle price follows GBM in debt-token-per-PT units",
            "Annual volatility and drift are assumptions in PR1",
            "PT collateral quantity held constant",
            f"Debt model: {request.simulation.debt_model.value}",
            "CONSTANT_APR, when selected, uses D0 * exp(apr * t / 365)",
            "No oracle hack simulated",
            "No protocol exploit simulated",
            "No liquidation execution simulated",
            "PT spot price is not used",
            "Chronos is not used",
            "FinBERT is not used",
            "Threshold breach frequency is a model-implied simulation frequency",
            "Losses are oracle mark-to-model values in loan-token units, not USD",
            "Wilson interval describes sampling uncertainty only",
        ],
    )

    def record(names: tuple[str, ...], calculate: Callable[[], tuple]) -> None:
        try:
            values = calculate()
            for value in values:
                if isinstance(value, Decimal) and not value.is_finite():
                    raise ValueError("Non-finite Decimal metric")
            for name, value in zip(names, values, strict=True):
                setattr(data, name, value)
        except _NUMERIC_ERRORS as exc:
            missing.extend(names)
            warnings.append(f"METRIC_UNAVAILABLE:{','.join(names)}:{exc}")

    q = request.collateral_quantity_pt
    p0 = request.initial_oracle_price_loan_per_pt
    d0 = request.debt_loan_units
    lltv = request.lltv
    record(("initial_ltv",), lambda: (d0 / (q * p0),))
    record(("initial_health_factor",), lambda: (lltv * (q * p0) / d0,))
    record(("critical_oracle_drop_ratio",), lambda: (
        metrics.critical_oracle_drop_ratio(q * p0, d0, lltv),
    ))
    already_breached = data.initial_ltv is not None and data.initial_ltv >= lltv
    if already_breached:
        warnings.append("ALREADY_ABOVE_LLTV")

    def record_breaches(count: int) -> None:
        data.breached_paths = count
        data.total_paths = request.num_paths
        data.simulated_threshold_breach_frequency = Decimal(count) / Decimal(request.num_paths)
        low, high = metrics.wilson_ci_95(count, request.num_paths)
        data.mc_sampling_ci_95 = MonteCarloSamplingCI(low=_decimal(low), high=_decimal(high))

    breach_fields = (
        "breached_paths", "total_paths", "simulated_threshold_breach_frequency",
        "mc_sampling_ci_95",
    )
    path_metrics = (
        "terminal_ltv_p50", "terminal_ltv_p95", "min_health_factor_p05",
        "oracle_mark_to_model_var95_loan_units", "oracle_mark_to_model_es95_loan_units",
    )
    if already_breached:
        record_breaches(request.num_paths)
    try:
        prices = simulate_oracle_paths(
            _float(p0, "p0"), _float(request.simulation.annual_volatility, "sigma"),
            _float(request.simulation.annual_drift, "mu"),
            request.horizon_days, request.num_paths, request.seed,
        )
        debt = debt_path(d0, request.horizon_days, request.simulation.debt_model,
                         request.simulation.borrow_apr)
        with np.errstate(over="ignore", under="ignore", invalid="ignore"):
            collateral = _float(q, "collateral quantity") * prices
        _finite(collateral, "collateral paths", positive=True)
    except _NUMERIC_ERRORS as exc:
        missing.extend(path_metrics)
        if not already_breached:
            missing.extend(breach_fields)
        warnings.append(f"SIMULATION_UNAVAILABLE:{exc}")
        return result(McStatus.PARTIAL, data)

    try:
        with np.errstate(over="ignore", under="ignore", divide="ignore", invalid="ignore"):
            ltv = debt[np.newaxis, :] / collateral
        _finite(ltv, "LTV paths", positive=True)
        if not already_breached:
            if data.initial_ltv is None:
                raise ValueError("Initial Decimal LTV unavailable for t=0 comparison")
            breached = ltv >= _float(lltv, "lltv")
            # Decimal decides t=0, avoiding a false breach due to float rounding.
            breached[:, 0] = False
            record_breaches(int(np.count_nonzero(np.any(breached, axis=1))))
        record(("terminal_ltv_p50", "terminal_ltv_p95"), lambda: tuple(
            _decimal(v) for v in metrics.terminal_ltv_quantiles(ltv[:, -1])
        ))
    except _NUMERIC_ERRORS as exc:
        missing.extend(("terminal_ltv_p50", "terminal_ltv_p95"))
        if not already_breached:
            missing.extend(breach_fields)
        warnings.append(f"LTV_UNAVAILABLE:{exc}")

    def hf_metric() -> tuple[Decimal]:
        with np.errstate(over="ignore", under="ignore", divide="ignore", invalid="ignore"):
            # Algebraically LLTV / LTV; avoids reciprocal overflow in LTV.
            hf = _float(lltv, "lltv") * collateral / debt[np.newaxis, :]
        _finite(hf, "health factor paths", positive=True)
        return (_decimal(metrics.min_health_factor_p05(np.min(hf, axis=1))),)

    record(("min_health_factor_p05",), hf_metric)

    def loss_metrics() -> tuple[Decimal, Decimal]:
        losses = metrics.mark_to_model_losses(
            float(collateral[0, 0]), float(debt[0]), collateral[:, -1],
            np.full(request.num_paths, debt[-1], dtype=np.float64),
        )
        var95, es95 = metrics.var_es_95(losses)
        return _decimal(var95), _decimal(es95)

    record(("oracle_mark_to_model_var95_loan_units", "oracle_mark_to_model_es95_loan_units"),
           loss_metrics)
    return result(McStatus.PARTIAL if missing else McStatus.OK, data)
