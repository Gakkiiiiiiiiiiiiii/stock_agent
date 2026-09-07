"""Narrow metrics boundary for knowledge-conclusion observability."""
from __future__ import annotations

from typing import Protocol


class KnowledgeConclusionMetrics(Protocol):
    def increment(self, name: str, **labels: str) -> None: ...
    def observe(self, name: str, value: float, **labels: str) -> None: ...
