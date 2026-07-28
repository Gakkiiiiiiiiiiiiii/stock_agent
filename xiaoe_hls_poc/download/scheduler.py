"""并发调度(13.4/13.5):线程池 1..8,每线程独立 HTTP Client。"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, TypeVar

from ..config import DEFAULT_WORKERS, MAX_WORKERS
from ..errors import ErrorCode, PocError

T = TypeVar("T")
R = TypeVar("R")


def clamp_workers(workers: int) -> int:
    if workers < 1 or workers > MAX_WORKERS:
        raise PocError(
            ErrorCode.INPUT_INVALID, f"workers 必须在 1..{MAX_WORKERS},当前 {workers}"
        )
    return workers or DEFAULT_WORKERS


class Scheduler:
    def __init__(self, workers: int = DEFAULT_WORKERS):
        self.workers = clamp_workers(workers)
        self._tls = threading.local()

    def run(
        self,
        items: Iterable[T],
        worker_factory: Callable[[], object],
        handler: Callable[[object, T], R],
        on_result: Callable[[T, R | None, Exception | None], None] | None = None,
    ) -> list[R]:
        """worker_factory() 每线程调用一次(构造独立 client);handler(ctx, item) 执行任务。"""
        results: list[R] = []

        def _get_ctx():
            if not hasattr(self._tls, "ctx"):
                self._tls.ctx = worker_factory()
            return self._tls.ctx

        def _run(item: T) -> R:
            return handler(_get_ctx(), item)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(_run, it): it for it in items}
            for fut in as_completed(futures):
                item = futures[fut]
                try:
                    res = fut.result()
                    results.append(res)
                    if on_result:
                        on_result(item, res, None)
                except Exception as exc:  # noqa: BLE001
                    if on_result:
                        on_result(item, None, exc)
                    else:
                        raise
        return results
