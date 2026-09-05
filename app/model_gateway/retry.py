from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from time import sleep, time
from typing import Any


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 8.0
    jitter: float = 0.0

    def delay(self, attempt: int, retry_after: float | None = None, *, random_value: float | None = None) -> float:
        if retry_after is not None:
            return max(0.0, min(float(retry_after), self.max_delay_seconds))
        value = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** max(0, attempt)))
        if self.jitter:
            value += value * self.jitter * (random.random() if random_value is None else random_value)
        return value


class RetryableError(RuntimeError):
    def __init__(self, message: str, *, retry_after: float | None = None, status_code: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.status_code = status_code


def parse_retry_after(value: Any, *, now: float | None = None) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(str(value)).timestamp()
            return max(0.0, target - (now if now is not None else time()))
        except (TypeError, ValueError, OverflowError):
            return None


def retry_call(
    operation: Callable[[], Any],
    *,
    policy: RetryPolicy | None = None,
    sleeper: Callable[[float], None] = sleep,
    retry_if: Callable[[Exception], bool] | None = None,
) -> Any:
    policy = policy or RetryPolicy()
    last: Exception | None = None
    for attempt in range(max(1, policy.max_attempts)):
        try:
            return operation()
        except Exception as exc:
            last = exc
            if attempt + 1 >= max(1, policy.max_attempts) or (retry_if and not retry_if(exc)):
                raise
            if retry_if is None and not isinstance(exc, RetryableError):
                raise
            sleeper(policy.delay(attempt, getattr(exc, "retry_after", None)))
    raise last or RuntimeError("retry operation did not run")
