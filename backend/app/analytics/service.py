"""In-process analytics facade with a bounded, thread-safe simulation cache."""
from collections import OrderedDict
from datetime import datetime, timezone
from decimal import localcontext
from threading import RLock

from app.decision.engine import decide
from app.stress.engine import simulate
from app.risk.scoring import current_risk


class AnalyticsService:
    def __init__(self, snapshot, *, replay=True, clock=None, max_simulations=256):
        self.snapshot = snapshot
        self.replay = replay
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_simulations = max_simulations
        self._simulations = OrderedDict()
        self._lock = RLock()

    def now(self):
        return self.snapshot.as_of if self.replay else self.clock()

    def simulate(self, request):
        with localcontext() as ctx:
            ctx.prec = 60
            result = simulate(self.snapshot, request, now=self.now())
        with self._lock:
            self._simulations[result.simulation_id] = result
            self._simulations.move_to_end(result.simulation_id)
            while len(self._simulations) > self.max_simulations:
                self._simulations.popitem(last=False)
        return result

    def decide(self, request):
        with self._lock:
            results = dict(self._simulations)
        with localcontext() as ctx:
            ctx.prec = 60
            now = self.now()
            risk = current_risk(self.snapshot, request.strategy_id, now)
            return decide(self.snapshot, request, results, now, risk)

    def risk(self, strategy_id):
        with localcontext() as ctx:
            ctx.prec = 60
            return current_risk(self.snapshot, strategy_id, self.now())
