from __future__ import annotations

from storage.repositories.vector_repository import MemoryRepository, VectorTaskRepository


def write_memory_and_enqueue(payload: dict, target_collection: str = "financial_memory", existing_memory_id: int | None = None) -> dict:
    repo = MemoryRepository()
    if existing_memory_id is not None:
        memory = repo.update(existing_memory_id, **payload)
    else:
        memory = repo.create(**payload)
    task = VectorTaskRepository().enqueue("memory_record", memory.id, target_collection)
    return {"memory_id": memory.id, "task_id": task.id, "target_collection": target_collection}


def enqueue_memory_reindex(memory_id: int, target_collection: str = "financial_memory") -> dict:
    task = VectorTaskRepository().enqueue("memory_record", memory_id, target_collection)
    return {"memory_id": memory_id, "task_id": task.id, "target_collection": target_collection}
