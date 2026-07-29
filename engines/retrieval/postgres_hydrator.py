from __future__ import annotations

from storage.repositories.knowledge_repository import KnowledgeRepository
from storage.repositories.vector_repository import MemoryRepository


class PostgresHydrator:
    def __init__(self) -> None:
        self.memory_repository = MemoryRepository()
        self.knowledge_repository = KnowledgeRepository()

    def hydrate(self, reranked_hits: list[dict]) -> list[dict]:
        records = []
        for item in reranked_hits:
            payload = item["payload"]
            record = None
            if payload.get("postgres_table") == "memory_record":
                memory = self.memory_repository.get(int(payload["postgres_id"]))
                record = None if memory is None else {
                    "id": memory.id,
                    "title": memory.title,
                    "content": memory.content,
                    "memory_type": memory.memory_type,
                    "source_type": memory.source_type,
                    "status": memory.status,
                    "related_regime": memory.related_regime,
                    "related_strategy": memory.related_strategy,
                    "related_theme": memory.related_theme,
                    "related_symbol": memory.related_symbol,
                    "source_date": memory.source_date.isoformat() if memory.source_date else None,
                    "valid_from": memory.valid_from.isoformat() if memory.valid_from else None,
                    "valid_to": memory.valid_to.isoformat() if memory.valid_to else None,
                }
            elif payload.get("postgres_table") == "knowledge_unit":
                unit = self.knowledge_repository.get_unit(int(payload["postgres_id"]))
                record = None if unit is None else {
                    "id": unit["id"],
                    "title": unit.get("subject_name") or unit.get("subject_key") or unit.get("knowledge_uid"),
                    "content": unit.get("canonical_statement") or unit.get("statement"),
                    "source_type": "video_knowledge_unit",
                    "status": unit.get("lifecycle_status"),
                    "primary_domain": unit.get("primary_domain"),
                    "knowledge_kind": unit.get("knowledge_kind"),
                    "temporal_class": unit.get("temporal_class"),
                    "verification_status": unit.get("verification_status"),
                    "subject_key": unit.get("subject_key"),
                    "conflict_key": unit.get("conflict_key"),
                    "conflict_group_id": unit.get("conflict_group_id"),
                    "as_of_time": unit.get("as_of_time"),
                    "valid_from": unit.get("valid_from"),
                    "valid_to": unit.get("valid_to"),
                    "decay_half_life_days": unit.get("decay_half_life_days"),
                    "evidence": unit.get("evidence") or [],
                    "entities": unit.get("entities") or [],
                }
            records.append(
                {
                    "source": payload.get("knowledge_kind", payload.get("memory_type", payload.get("source_type", "memory"))),
                    "postgres_id": payload.get("postgres_id"),
                    "title": payload.get("title"),
                    "status": payload.get("status"),
                    "source_type": payload.get("source_type"),
                    "related_strategy": payload.get("related_strategy"),
                    "related_theme": payload.get("related_theme"),
                    "related_symbol": payload.get("related_symbol"),
                    "source_date": payload.get("source_date"),
                    "source_timestamp": payload.get("source_timestamp"),
                    "content": record["content"] if record else item["text"],
                    "record": record,
                    "dense_score": item.get("dense_score"),
                    "bm25_score": item.get("bm25_score"),
                    "sparse_recall_score": item.get("sparse_recall_score"),
                    "rerank_score": item.get("rerank_score"),
                    "final_score": item.get("final_score"),
                }
            )
        return records
