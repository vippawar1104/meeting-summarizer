import time
from collections.abc import Callable
from typing import Literal

State = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    """Stops sending traffic to a failing provider, then probes it with a single trial call."""

    def __init__(
        self,
        threshold: int = 3,
        cooldown_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self.state: State = "closed"
        self._failures = 0
        self._opened_at = 0.0
        self._trial_in_flight = False

    def allow(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if self._clock() - self._opened_at < self.cooldown_s:
                return False
            self.state = "half_open"
            self._trial_in_flight = False
        if self._trial_in_flight:
            return False
        self._trial_in_flight = True
        return True

    def record_success(self) -> None:
        self.state = "closed"
        self._failures = 0
        self._trial_in_flight = False

    def record_failure(self) -> None:
        self._trial_in_flight = False
        if self.state == "half_open":
            self._trip()
            return
        self._failures += 1
        if self._failures >= self.threshold:
            self._trip()

    def _trip(self) -> None:
        self.state = "open"
        self._opened_at = self._clock()
        self._failures = 0
