"""Regression checks for hypothetical oracle-price Monte Carlo PR1."""
import ast
from copy import deepcopy
from decimal import Decimal
import json
import math
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

from app.stress.monte_carlo import run_monte_carlo
from app.stress.monte_carlo.metrics import wilson_ci_95
from app.stress.monte_carlo.models import McStatus, MonteCarloRequest

APP_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def payload():
    return json.loads((APP_DIR / "fixtures/monte_carlo_synthetic.json").read_text(encoding="utf-8-sig"))


@pytest.fixture
def request_model(payload):
    return MonteCarloRequest.model_validate(payload)


@pytest.fixture
def result(request_model):
    value = run_monte_carlo(request_model)
    assert value.status == McStatus.OK
    assert value.data is not None
    return value


def simulate(payload, **changes):
    values = deepcopy(payload)
    values.update(changes)
    return run_monte_carlo(MonteCarloRequest.model_validate(values))


def assert_finite_tree(value):
    if isinstance(value, Decimal):
        assert value.is_finite()
    elif isinstance(value, float):
        assert math.isfinite(value)
    elif isinstance(value, dict):
        for child in value.values():
            assert_finite_tree(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            assert_finite_tree(child)


def test_initial_ltv(result):
    assert result.data.initial_ltv == Decimal("0.6")


def test_initial_health_factor(result):
    assert result.data.initial_health_factor == Decimal("1.525")


def test_critical_oracle_drop(result):
    assert abs(result.data.critical_oracle_drop_ratio - Decimal("0.344262295081967")) < Decimal("1e-15")


def test_zero_volatility_healthy_position(payload):
    payload["simulation"].update(annual_volatility="0", annual_drift="0")
    value = simulate(payload)
    assert value.status == McStatus.OK
    assert value.data.breached_paths == 0
    assert value.data.simulated_threshold_breach_frequency == 0


def test_already_above_lltv_includes_time_zero(payload):
    # A strongly rising price makes t=0 inclusion observable.
    payload["simulation"].update(annual_volatility="0", annual_drift="100")
    value = simulate(payload, debt_loan_units="1900000")
    assert value.data.breached_paths == value.data.total_paths == 10000
    assert value.data.simulated_threshold_breach_frequency == 1
    assert "ALREADY_ABOVE_LLTV" in value.warnings


def test_same_seed_reproducible(request_model, result):
    assert run_monte_carlo(request_model).model_dump() == result.model_dump()


def test_different_seed_changes_stochastic_metrics(payload, result):
    other = simulate(payload, seed=43)
    fields = ("terminal_ltv_p50", "terminal_ltv_p95", "min_health_factor_p05",
              "oracle_mark_to_model_var95_loan_units", "oracle_mark_to_model_es95_loan_units")
    assert any(getattr(other.data, field) != getattr(result.data, field) for field in fields)


def test_supply_not_applicable(payload):
    value = simulate(payload, position_side="SUPPLY")
    assert value.status == McStatus.NOT_APPLICABLE and value.data is None
    assert "SUPPLY_POSITION_NO_BORROWER_LIQUIDATION" in value.warnings


def test_no_debt_not_applicable(payload):
    value = simulate(payload, debt_loan_units="0")
    assert value.status == McStatus.NOT_APPLICABLE and value.data is None
    assert "NO_DEBT" in value.warnings


@pytest.mark.parametrize("expiry", ["2026-10-11T12:00:00Z", "2026-10-17T12:00:00Z"])
def test_expiry_within_or_at_horizon(payload, expiry):
    value = simulate(payload, pt_expiry=expiry)
    assert value.status == McStatus.UNAVAILABLE and value.data is None
    assert "PT_EXPIRES_WITHIN_HORIZON" in value.warnings
    assert value.missing_fields == ["valid_horizon"]


def test_frequency_bounds(result):
    assert 0 <= result.data.simulated_threshold_breach_frequency <= 1


def test_breach_count_bounds(result):
    assert 0 <= result.data.breached_paths <= result.data.total_paths == 10000


def test_terminal_quantile_order(result):
    assert result.data.terminal_ltv_p50 <= result.data.terminal_ltv_p95


def test_all_numeric_results_finite(result):
    assert_finite_tree(result.model_dump())


def test_no_realized_liquidation_loss(result):
    assert result.data.realized_liquidation_loss_usd is None


def test_zero_lltv_rejected(payload):
    payload["lltv"] = "0"
    with pytest.raises(ValidationError, match="lltv"):
        MonteCarloRequest.model_validate(payload)


def test_lltv_above_one_rejected(payload):
    payload["lltv"] = "1.2"
    with pytest.raises(ValidationError, match="lltv"):
        MonteCarloRequest.model_validate(payload)


def test_too_few_paths_rejected(payload):
    payload["num_paths"] = 999
    with pytest.raises(ValidationError, match="num_paths"):
        MonteCarloRequest.model_validate(payload)


def test_too_many_paths_rejected(payload):
    payload["num_paths"] = 50001
    with pytest.raises(ValidationError, match="num_paths"):
        MonteCarloRequest.model_validate(payload)


@pytest.mark.parametrize("field", ["collateral_quantity_pt", "initial_oracle_price_loan_per_pt", "debt_loan_units", "lltv"])
def test_python_float_rejected(payload, field):
    payload[field] = float(payload[field])
    with pytest.raises(ValidationError, match="float"):
        MonteCarloRequest.model_validate(payload)


def test_constant_apr_requires_rate(payload):
    payload["simulation"]["debt_model"] = "CONSTANT_APR"
    with pytest.raises(ValidationError, match="borrow_apr"):
        MonteCarloRequest.model_validate(payload)


def test_real_data_requires_evidence(payload):
    payload["input_scope"] = "REAL_DATA"
    with pytest.raises(ValidationError, match="evidence_ids"):
        MonteCarloRequest.model_validate(payload)


def test_observed_oracle_series_deferred_to_pr2(payload):
    payload["simulation"]["source_mode"] = "OBSERVED_ORACLE_SERIES"
    with pytest.raises(ValidationError, match="PR2"):
        MonteCarloRequest.model_validate(payload)


def test_wilson_more_paths_narrower_at_same_frequency():
    # Hold the observed proportion fixed: otherwise the comparison is confounded.
    low_small, high_small = wilson_ci_95(100, 1000)
    low_large, high_large = wilson_ci_95(5000, 50000)
    assert 0 <= low_large < high_large <= 1
    assert high_large - low_large < high_small - low_small


def test_json_is_finite_and_decimals_are_strings(request_model, result):
    def compare(python_value, json_value):
        if isinstance(python_value, Decimal):
            assert isinstance(json_value, str)
            assert Decimal(json_value).is_finite()
            assert Decimal(json_value) == python_value
        elif isinstance(python_value, dict):
            for key, child in python_value.items():
                compare(child, json_value[key])
        elif isinstance(python_value, (list, tuple)):
            for child, encoded in zip(python_value, json_value, strict=True):
                compare(child, encoded)
    for model in (request_model, result):
        encoded = model.model_dump(mode="json")
        text = json.dumps(encoded, allow_nan=False)
        assert "NaN" not in text and "Infinity" not in text
        compare(model.model_dump(), encoded)


def test_no_ml_imports_in_monte_carlo():
    # Only stdlib, NumPy, Pydantic and local numerical modules are permitted.
    # AST inspection avoids false positives on explanatory warning strings.
    allowed = set(sys.stdlib_module_names) | {"numpy", "pydantic"}
    sources = list((APP_DIR / "stress/monte_carlo").rglob("*.py"))
    assert sources
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] in allowed, (path, alias.name)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                assert node.module.split(".")[0] in allowed, (path, node.module)
            elif isinstance(node, ast.Call):
                # Dynamic imports could bypass the static dependency check.
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
                assert name not in {"__import__", "import_module"}, path
