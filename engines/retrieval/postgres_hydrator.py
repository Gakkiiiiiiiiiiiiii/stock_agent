from __future__ import annotations

from storage.repositories.vector_repository import MemoryRepository


class PostgresHydrator:
    def __init__(self) -> None:
        self.memory_repository = MemoryRepository()

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
            records.append(
                {
                    "source": payload.get("memory_type", payload.get("source_type", "memory")),
                    "postgres_id": payload.get("postgres_id"),
                    "title": payload.get("title"),
                    "status": payload.get("status"),
                    "rerank_score": item["rerank_score"],
                    "source_type": payload.get("source_type"),
                    "related_strategy": payload.get("related_strategy"),
                    "related_theme": payload.get("related_theme"),
                    "related_symbol": payload.get("related_symbol"),
                    "source_date": payload.get("source_date"),
                    "source_timestamp": payload.get("source_timestamp"),
                    "content": record["content"] if record else item["text"],
                    "record": record,
                }
            )
        return records
