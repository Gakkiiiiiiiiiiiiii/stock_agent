"""终端进度展示(Rich)。不输出任何敏感信息。"""

from __future__ import annotations

from ..models import SegmentTask
from ..security.redactor import redact_url


class ConsoleProgress:
    """简洁的逐分片进度回调(默认静默,仅统计;verbose 时逐条打印)。"""

    def __init__(self, total: int, *, verbose: bool = False, output_fn=print):
        self.total = total
        self.done = 0
        self.failed = 0
        self.verbose = verbose
        self._out = output_fn

    def __call__(self, task: SegmentTask, status: str) -> None:
        self.done += 1
        if status == "fail":
            self.failed += 1
        if self.verbose:
            self._out(f"  [{self.done}/{self.total}] seg {task.index}: {status}")

    def summary(self) -> str:
        return f"分片完成 {self.done - self.failed}/{self.total},失败 {self.failed}"
