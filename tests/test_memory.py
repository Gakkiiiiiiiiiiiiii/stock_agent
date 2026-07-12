from datetime import UTC, datetime

from engines.memory.memory_writer import write_memory_and_enqueue
from storage.bootstrap import create_all
from storage.repositories.vector_repository import MemoryRepository, VectorTaskRepository


def test_write_memory_and_enqueue_task():
    create_all()
    result = write_memory_and_enqueue(
        {
            "memory_type": "trade_review_lesson",
            "title": "B2 失败案例",
            "content": "轮动行情中追高失败。",
            "source_type": "trade_review",
            "source_date": datetime.now(UTC),
            "related_regime": "rotation_market",
            "related_strategy": "B2",
            "status": "validated",
            "importance": "high",
            "confidence": 0.82,
        }
    )
    memory = MemoryRepository().get(result["memory_id"])
    task = VectorTaskRepository().next_pending()
    assert memory is not None
    assert task is not None
    assert task.postgres_id == memory.id
