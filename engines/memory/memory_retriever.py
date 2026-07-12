from __future__ import annotations

from engines.retrieval.hybrid_retriever import HybridRetriever


def retrieve_memory(query: str, filters: dict | None = None, top_k: int = 5) -> dict:
    return HybridRetriever().retrieve(query=query, task_type="memory_lookup", filters=filters, top_k=top_k)

