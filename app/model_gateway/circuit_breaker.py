from __future__ import annotations

from enum import StrEnum
from time import monotonic
from typing import Callable, TypeVar

T = TypeVar("T")


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(RuntimeError):
    code = "DEPENDENCY_UNAVAILABLE"


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int = 3, recovery_seconds: float = 30.0, clock: Callable[[], float] = monotonic) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_seconds = max(0.0, recovery_seconds)
        self.clock = clock
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.opened_at: float | None = None

    def allow(self) -> bool:
        if self.state is CircuitState.OPEN:
            if self.opened_at is not None and self.clock() - self.opened_at >= self.recovery_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True

    def before_call(self) -> None:
        if not self.allow():
            raise CircuitOpenError("circuit breaker is open")

    def success(self) -> None:
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.opened_at = None

    def failure(self) -> None:
        self.failures += 1
        if self.state is CircuitState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = self.clock()

    def call(self, operation: Callable[[], T]) -> T:
        self.before_call()
        try:
            result = operation()
        except Exception:
            self.failure()
            raise
        self.success()
        return result
