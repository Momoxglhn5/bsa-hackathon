"""Manual Monte Carlo demo using synthetic assumptions only; no live data.

From backend/: python scripts/test_monte_carlo_manual.py
"""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.stress.monte_carlo import run_monte_carlo  # noqa: E402
from app.stress.monte_carlo.models import (  # noqa: E402
    MODEL_VERSION,
    DebtModel,
    InputScope,
    MonteCarloRequest,
    SimulationParams,
    SourceMode,
)


def main() -> None:
    request = MonteCarloRequest(
        strategy_id="strat:demo",
        position_id="sim:fund-001",
        snapshot_as_of=datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
        input_scope=InputScope.HYPOTHETICAL,
        loan_token_symbol="USDC",
        loan_token_address=None,
        collateral_quantity_pt=Decimal("2000000"),
        initial_oracle_price_loan_per_pt=Decimal("1"),
        debt_loan_units=Decimal("1200000"),
        lltv=Decimal("0.915"),
        horizon_days=7,
        num_paths=10000,
        seed=42,
        pt_expiry=None,
        simulation=SimulationParams(
            model=MODEL_VERSION,
            source_mode=SourceMode.USER_ASSUMPTION,
            annual_volatility=Decimal("0.80"),
            annual_drift=Decimal("0"),
            debt_model=DebtModel.FROZEN_DEBT,
            borrow_apr=None,
        ),
        evidence_ids=[],
        warnings=[],
    )
    result = run_monte_carlo(request)
    print("SYNTHETIC DEMO - hypothetical inputs; no real or live position data.")
    print("Frequency is model-implied, not a real-world liquidation probability.")
    print("VaR95/ES95 are oracle mark-to-model losses in loan-token units (USDC),")
    print("not realized liquidation losses or verified USD values.")
    print(f"status = {result.status.value}")
    data = result.data
    if data is None:
        print("data = None (simulation results unavailable or not applicable)")
    else:
        for label, value in (
            ("initial_ltv", data.initial_ltv),
            ("initial_health_factor", data.initial_health_factor),
            ("critical_oracle_drop_ratio", data.critical_oracle_drop_ratio),
            ("breached_paths", data.breached_paths),
            ("total_paths", data.total_paths),
            ("frequency", data.simulated_threshold_breach_frequency),
            ("Wilson CI", None if data.mc_sampling_ci_95 is None else
             (data.mc_sampling_ci_95.low, data.mc_sampling_ci_95.high)),
            ("terminal LTV p50", data.terminal_ltv_p50),
            ("terminal LTV p95", data.terminal_ltv_p95),
            ("min HF p05", data.min_health_factor_p05),
            ("VaR95 (loan-token units)", data.oracle_mark_to_model_var95_loan_units),
            ("ES95 (loan-token units)", data.oracle_mark_to_model_es95_loan_units),
        ):
            print(f"{label} = {value}")
    print(f"missing_fields = {result.missing_fields}")
    print("warnings:")
    for warning in result.warnings:
        print(f"- {warning}")
    print("assumptions:")
    if data is None:
        print("None (no simulation data)")
    else:
        for assumption in data.assumptions:
            print(f"- {assumption}")


if __name__ == "__main__":
    main()
