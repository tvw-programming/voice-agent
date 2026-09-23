import time


class CircuitBreaker:
    """Per-provider circuit breaker: after N consecutive failures, skip the
    provider for `cooldown` seconds, then allow one trial request."""

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 60, clock=time.monotonic):
        self.threshold = failure_threshold
        self.cooldown = cooldown_seconds
        self.clock = clock
        self.failures: dict[str, int] = {}
        self.opened_at: dict[str, float] = {}

    def is_open(self, name: str) -> bool:
        opened = self.opened_at.get(name)
        if opened is None:
            return False
        if self.clock() - opened >= self.cooldown:
            # Half-open: allow a trial; a single further failure reopens it.
            del self.opened_at[name]
            self.failures[name] = self.threshold - 1
            return False
        return True

    def success(self, name: str) -> None:
        self.failures[name] = 0
        self.opened_at.pop(name, None)

    def failure(self, name: str) -> None:
        self.failures[name] = self.failures.get(name, 0) + 1
        if self.failures[name] >= self.threshold:
            self.opened_at[name] = self.clock()

    def trip(self, name: str) -> None:
        self.failures[name] = self.threshold
        self.opened_at[name] = self.clock()
