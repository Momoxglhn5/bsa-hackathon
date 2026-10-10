"""Read-only runtime gate and atomic offline JSON export, without torch."""
import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from ..models.evidence import DataMode
from ..models.strategy import StrategyDTO, StrategyStatus
from .contracts import ForecastBundle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CacheResult:
    bundle: ForecastBundle | None
    reason: str | None


def cache_path(root: Path, strategy_id: str) -> Path:
    # Digest prevents traversal AND collisions from lossy character replacement.
    safe_id = hashlib.sha256(strategy_id.encode()).hexdigest()
    return root / safe_id / "PT_MARKET_PRICE_USD" / "forecast.json"


def export_bundle(root: Path, bundle: ForecastBundle) -> Path:
    bundle = ForecastBundle.model_validate_json(bundle.model_dump_json())
    path = cache_path(root, bundle.strategy_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as output:
            temporary = output.name
            output.write(bundle.model_dump_json(indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return path


class ForecastCache:
    def __init__(self, root: Path, ttl: timedelta = timedelta(hours=24)):
        if ttl <= timedelta(0):
            raise ValueError("cache TTL must be positive")
        self.root = root
        self.ttl = ttl

    def load(self, strategy: StrategyDTO, *, horizon_days: int, now: datetime) -> CacheResult:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone aware")
        if horizon_days not in (1, 3, 7):
            return CacheResult(None, "FORECAST_NOT_VALIDATED")
        try:
            bundle = ForecastBundle.model_validate_json(
                cache_path(self.root, strategy.strategy_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return CacheResult(None, "FORECAST_UNAVAILABLE")
        except (OSError, ValueError, ValidationError):
            logger.warning("Forecast cache unreadable or invalid for %s", strategy.strategy_id)
            return CacheResult(None, "FORECAST_NOT_VALIDATED")
        if bundle.status == "STALE":
            return CacheResult(None, "FORECAST_STALE")
        if (bundle.status != "VALIDATED" or strategy.data_mode != DataMode.REAL
                or strategy.status != StrategyStatus.VERIFIED_ACTIVE
                or strategy.data_status.value not in ("OK", "PARTIAL")
                or bundle.strategy_id != strategy.strategy_id
                or bundle.entity_id != f"pendle:1:{strategy.pendle_market_address}"):
            return CacheResult(None, "FORECAST_NOT_VALIDATED")
        if bundle.generated_at > now or bundle.latest_observation_at > now:
            return CacheResult(None, "FORECAST_NOT_VALIDATED")
        if now - bundle.latest_observation_at > self.ttl:
            return CacheResult(None, "FORECAST_STALE")
        if (not strategy.expiry or now >= strategy.expiry
                or bundle.latest_observation_at + timedelta(days=7) >= strategy.expiry):
            return CacheResult(None, "FORECAST_NOT_VALIDATED")
        return CacheResult(bundle, None)

    def for_mode(self, mode: Literal["normal", "predictive"], strategy: StrategyDTO,
                 *, horizon_days: int, now: datetime) -> CacheResult:
        if mode == "normal":
            return CacheResult(None, None)  # No disk access in Normal.
        if mode != "predictive":
            raise ValueError("unsupported decision mode")
        return self.load(strategy, horizon_days=horizon_days, now=now)
