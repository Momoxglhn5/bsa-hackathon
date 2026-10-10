"""Public entry points for the internal Monte Carlo module (PR1 + PR2 calibration)."""
from .simulation import debt_path, run_monte_carlo, simulate_oracle_paths

__all__ = ["simulate_oracle_paths", "debt_path", "run_monte_carlo"]
