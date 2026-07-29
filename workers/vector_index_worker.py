from __future__ import annotations

import os
import time

from engines.retrieval.chunker import chunk_text
from engines.retrieval.collection_manifest import validate_embedding_manifest
from engines.retrieval.embedder import build_embedder
from engines.retrieval.qdrant_client import FinancialQdrantClient
from storage.bootstrap import create_all
from storage.repositories.knowledge_repository import KnowledgeRepository
from storage.repositories.vector_repository import MemoryRepository, VectorMappingRepository, VectorTaskRepository


def process_one_task() -> bool:
    task_repo = VectorTaskRepository()
    task = task_repo.next_pending()
    if task is None:
        return False
    try:
        qdrant = FinancialQdrantClient()
        qdrant.ensure_collections()
        embedder = build_embedder()
        embedding_meta = embedder.metadata
        validate_embedding_manifest(task.target_collection, embedding_meta)
        if task.postgres_table not in {"memory_record", "knowledge_unit"}:
            raise ValueError(f"unsupported postgres_table: {task.postgres_table}")
        if task.postgres_table == "knowledge_unit":
            unit = KnowledgeRepository().get_unit(task.postgres_id)
            if unit is None:
                raise ValueError(f"knowledge unit not found: {task.postgres_id}")
            qdrant.delete_by_payload(
                task.target_collection,
                {"postgres_table": "knowledge_unit", "postgres_id": unit["id"]},
            )
            VectorMappingRepository().delete_for_record("knowledge_unit", unit["id"])
            if unit.get("lifecycle_status") in {"REJECTED", "RETIRED"}:
                task_repo.mark_success(task.id)
                return True
            text = "\n".join(
                part
                for part in [
                    unit.get("canonical_statement") or unit.get("statement"),
                    f"条件：{unit.get('condition_text')}" if unit.get("condition_text") else "",
                    f"证伪：{unit.get('invalidation_text')}" if unit.get("invalidation_text") else "",
                    "证据：" + "；".join(evidence.get("evidence_text", "") for evidence in unit.get("evidence", [])[:3]),
                ]
                if part
            )
            payload_base = {
                "postgres_table": "knowledge_unit",
                "postgres_id": unit["id"],
                "knowledge_uid": unit.get("knowledge_uid"),
                "source_video_id": unit.get("source_video_id"),
                "source_chapter_id": unit.get("source_chapter_id"),
                "primary_domain": unit.get("primary_domain"),
                "knowledge_kind": unit.get("knowledge_kind"),
                "temporal_class": unit.get("temporal_class"),
                "expression_type": unit.get("expression_type"),
                "subject_type": unit.get("subject_type"),
                "subject_key": unit.get("subject_key"),
                "subject_name": unit.get("subject_name"),
                "predicate_key": unit.get("predicate_key"),
                "claim_type": unit.get("claim_type"),
                "sentiment": unit.get("sentiment"),
                "lifecycle_status": unit.get("lifecycle_status"),
                "verification_status": unit.get("verification_status"),
                "as_of_time": unit.get("as_of_time"),
                "valid_from": unit.get("valid_from"),
                "valid_to": unit.get("valid_to"),
                "time_horizon": unit.get("time_horizon"),
                "timeframe": unit.get("timeframe"),
                "conflict_key": unit.get("conflict_key"),
                "conflict_group_id": unit.get("conflict_group_id"),
                "confidence": unit.get("extraction_confidence"),
                "source_type": "video_knowledge_unit",
                "version": "v3",
            }
            for chunk in chunk_text(text):
                payload = payload_base | {
                    "chunk_id": f"knowledge_unit_{unit['id']}_{chunk['chunk_id']}",
                    "content_hash": chunk["content_hash"],
                    "text": chunk["text"],
                    "embedding_provider": embedding_meta.provider,
                    "embedding_model": embedding_meta.model,
                    "embedding_dimension": embedding_meta.dimension,
                }
                point_id = qdrant.upsert_chunk(task.target_collection, embedder.embed(chunk["text"]), payload)
                VectorMappingRepository().upsert(
                    postgres_table="knowledge_unit",
                    postgres_id=unit["id"],
                    chunk_id=payload["chunk_id"],
                    qdrant_collection=task.target_collection,
                    qdrant_point_id=point_id,
                    content_hash=chunk["content_hash"],
                    embedding_model=f"{embedding_meta.provider}:{embedding_meta.model}:{embedding_meta.dimension}",
                    reranker_model=f"{os.getenv('RERANKER_PROVIDER', 'unknown')}:{os.getenv('RERANKER_MODEL', 'unknown')}",
                )
            task_repo.mark_success(task.id)
            return True
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
                "embedding_provider": embedding_meta.provider,
                "embedding_model": embedding_meta.model,
                "embedding_dimension": embedding_meta.dimension,
            }
            point_id = qdrant.upsert_chunk(task.target_collection, embedder.embed(chunk["text"]), payload)
            VectorMappingRepository().upsert(
                postgres_table="memory_record",
                postgres_id=memory.id,
                chunk_id=payload["chunk_id"],
                qdrant_collection=task.target_collection,
                qdrant_point_id=point_id,
                content_hash=chunk["content_hash"],
                embedding_model=f"{embedding_meta.provider}:{embedding_meta.model}:{embedding_meta.dimension}",
                reranker_model=f"{os.getenv('RERANKER_PROVIDER', 'unknown')}:{os.getenv('RERANKER_MODEL', 'unknown')}",
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
