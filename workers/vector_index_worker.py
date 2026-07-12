from __future__ import annotations

import time

from engines.retrieval.chunker import chunk_text
from engines.retrieval.embedder import DeterministicEmbedder
from engines.retrieval.qdrant_client import FinancialQdrantClient
from storage.bootstrap import create_all
from storage.repositories.vector_repository import MemoryRepository, VectorMappingRepository, VectorTaskRepository


def process_one_task() -> bool:
    task_repo = VectorTaskRepository()
    task = task_repo.next_pending()
    if task is None:
        return False
    try:
        qdrant = FinancialQdrantClient()
        qdrant.ensure_collections()
        embedder = DeterministicEmbedder()
        if task.postgres_table != "memory_record":
            raise ValueError(f"unsupported postgres_table: {task.postgres_table}")
        memory = MemoryRepository().get(task.postgres_id)
        if memory is None:
            raise ValueError(f"memory record not found: {task.postgres_id}")
        qdrant.delete_by_payload(
            task.target_collection,
            {"postgres_table": "memory_record", "postgres_id": memory.id},
        )
        VectorMappingRepository().delete_for_record("memory_record", memory.id)
        if memory.is_deleted:
            task_repo.mark_success(task.id)
            return True
        payload_base = {
            "postgres_table": "memory_record",
            "postgres_id": memory.id,
            "memory_type": memory.memory_type,
            "title": memory.title,
            "related_regime": memory.related_regime,
            "related_strategy": memory.related_strategy,
            "related_theme": memory.related_theme,
            "related_symbol": memory.related_symbol,
            "status": memory.status,
            "importance": memory.importance,
            "confidence": memory.confidence,
            "source_type": memory.source_type,
            "source_date": memory.source_date.isoformat() if memory.source_date else None,
            "source_timestamp": int(memory.source_date.timestamp()) if memory.source_date else None,
            "valid_from": memory.valid_from.isoformat() if memory.valid_from else None,
            "valid_to": memory.valid_to.isoformat() if memory.valid_to else None,
            "is_deleted": memory.is_deleted,
            "recency_priority": "latest_wins",
            "version": "v1",
        }
        for chunk in chunk_text(memory.content):
            payload = payload_base | {
                "chunk_id": f"memory_record_{memory.id}_{chunk['chunk_id']}",
                "content_hash": chunk["content_hash"],
                "text": chunk["text"],
            }
            point_id = qdrant.upsert_chunk(task.target_collection, embedder.embed(chunk["text"]), payload)
            VectorMappingRepository().upsert(
                postgres_table="memory_record",
                postgres_id=memory.id,
                chunk_id=payload["chunk_id"],
                qdrant_collection=task.target_collection,
                qdrant_point_id=point_id,
                content_hash=chunk["content_hash"],
                embedding_model="deterministic-local",
                reranker_model="local-lexical-reranker",
            )
        task_repo.mark_success(task.id)
    except Exception as exc:
        task_repo.mark_failed(task.id, str(exc))
    return True


def main() -> None:
    last_error = None
    for _ in range(30):
        try:
            create_all()
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            time.sleep(2)
    if last_error is not None:
        raise last_error
    while True:
        processed = process_one_task()
        if not processed:
            time.sleep(2)


if __name__ == "__main__":
    main()
