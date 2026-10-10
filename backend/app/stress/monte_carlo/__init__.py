"""Public entry points for the internal Monte Carlo PR1 module."""
from .simulation import debt_path, run_monte_carlo, simulate_oracle_paths

__all__ = ["simulate_oracle_paths", "debt_path", "run_monte_carlo"]
