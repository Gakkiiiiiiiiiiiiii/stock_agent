from __future__ import annotations

from engines.memory.memory_retriever import retrieve_memory
from storage.repositories.vector_repository import VectorTaskRepository


def retrieve_relevant_context(query: str, task_type: str | None = None, filters: dict | None = None, top_k: int = 5) -> dict:
    return retrieve_memory(query=query, filters=filters, top_k=top_k) | {"task_type": task_type}


def hybrid_search_memory(query: str, collections: list[str] | None = None, filters: dict | None = None, top_n: int = 10) -> dict:
    result = retrieve_memory(query=query, filters=filters, top_k=top_n)
    if collections:
        result["collections"] = collections
    return result


def index_memory_to_qdrant(postgres_table: str, postgres_id: int, target_collection: str) -> dict:
    if postgres_table != "memory_record":
        return {"status": "unsupported", "postgres_table": postgres_table}
    task = VectorTaskRepository().enqueue(postgres_table, postgres_id, target_collection)
    return {"status": "enqueued", "task_id": task.id, "target_collection": target_collection}
