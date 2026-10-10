"""Run 1/3/7-day hypothetical oracle simulations from the synthetic fixture.

From backend/: python scripts/run_monte_carlo_demo.py
Writes only app/fixtures/monte_carlo_demo_result.json; makes no network calls.
"""
import json
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.stress.monte_carlo import run_monte_carlo  # noqa: E402
from app.stress.monte_carlo.models import InputScope, MonteCarloRequest  # noqa: E402


def main() -> None:
    fixture_dir = BACKEND_DIR / "app/fixtures"
    payload = json.loads((fixture_dir / "monte_carlo_synthetic.json").read_text(encoding="utf-8-sig"))
    base_request = MonteCarloRequest.model_validate(payload)
    if base_request.input_scope != InputScope.HYPOTHETICAL:
        raise ValueError("This demo accepts HYPOTHETICAL input only")
    print("HYPOTHETICAL - SYNTHETIC_FIXTURE_NOT_LIVE")
    print("NOT_A_REAL_WORLD_LIQUIDATION_PROBABILITY")
    print("Oracle mark-to-model losses in loan-token units; not real liquidation losses or USD estimates.")
    runs = []
    for horizon in (1, 3, 7):
        request = MonteCarloRequest.model_validate(payload | {"horizon_days": horizon})
        result = run_monte_carlo(request)
        print(f"\nHorizon: {horizon} day(s); status: {result.status.value}")
        data = result.data
        if data is None:
            print(f"No simulation data: {result.warnings}")
        else:
            ci = data.mc_sampling_ci_95
            for label, value in (
                ("breach frequency (model-implied)", data.simulated_threshold_breach_frequency),
                ("Wilson CI", None if ci is None else f"[{ci.low}, {ci.high}]"),
                ("terminal LTV p95", data.terminal_ltv_p95),
                ("min HF p05", data.min_health_factor_p05),
                ("VaR95 (loan-token units)", data.oracle_mark_to_model_var95_loan_units),
                ("ES95 (loan-token units)", data.oracle_mark_to_model_es95_loan_units),
            ):
                print(f"{label} = {value}")
        print(f"missing_fields = {result.missing_fields}")
        print(f"warnings = {result.warnings}")
        runs.append({"request": request.model_dump(mode="json"),
                     "result": result.model_dump(mode="json")})
    output = {
        "input_scope": "HYPOTHETICAL",
        "warnings": ["SYNTHETIC_FIXTURE_NOT_LIVE", "NOT_A_REAL_WORLD_LIQUIDATION_PROBABILITY"],
        "description": "Synthetic oracle-price GBM demo; no live data or real positions. Frequencies are model-implied. Losses are in loan-token units, not realized liquidation losses.",
        "runs": runs,
    }
    target = fixture_dir / "monte_carlo_demo_result.json"
    target.write_text(json.dumps(output, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"\nSynthetic results saved to: {target}")


if __name__ == "__main__":
    main()
