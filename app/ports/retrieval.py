"""Application boundary for read-only retrieval."""
from __future__ import annotations

from typing import Protocol


class RetrievalPort(Protocol):
    def retrieve_relevant_context(
        self,
        query: str,
        task_type: str | None = None,
        filters: dict | None = None,
        top_k: int = 5,
    ) -> dict: ...
