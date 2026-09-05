"""Local retrieval adapter over the storage-backed memory retriever."""
from __future__ import annotations

from engines.memory.memory_retriever import retrieve_memory


class LocalRetrievalAdapter:
    def retrieve_relevant_context(
        self,
        query: str,
        task_type: str | None = None,
        filters: dict | None = None,
        top_k: int = 5,
    ) -> dict:
        return retrieve_memory(query=query, filters=filters, top_k=top_k) | {"task_type": task_type}
