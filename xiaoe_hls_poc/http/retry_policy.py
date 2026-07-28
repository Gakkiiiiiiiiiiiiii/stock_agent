"""重试策略(13.6):有界重试 + 指数退避;401/403 不重试。"""

from __future__ import annotations

import random

RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
NON_RETRYABLE_STATUS = frozenset({400, 401, 403, 404})


class RetryPolicy:
    def __init__(
        self,
        max_attempts: int = 4,
        base_delay: float = 1.0,
        max_delay: float = 16.0,
        jitter: float = 0.5,
    ):
        self.max_attempts = max(1, max_attempts)
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter

    def backoff(self, attempt: int) -> float:
        """attempt 从 1 开始。"""
        delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        return delay + random.uniform(0, self.jitter)

    def is_retryable_status(self, status: int) -> bool:
        return status in RETRYABLE_STATUS

    def is_retryable_exception(self, exc: BaseException) -> bool:
        import httpx

        return isinstance(
            exc,
            (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            ),
        )
