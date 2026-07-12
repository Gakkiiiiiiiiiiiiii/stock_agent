from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from sqlalchemy import create_engine

from engines.memory.memory_writer import write_memory_and_enqueue
from storage.db import Base, SessionLocal
from storage.repositories.vector_repository import MemoryRepository


def configure_test_db(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'memory_writer_test.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_write_memory_and_enqueue_updates_existing_record():
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="memory-writer-test-", dir=temp_root))
    configure_test_db(tmp_path)
    repo = MemoryRepository()
    try:
        created = write_memory_and_enqueue(
            {
                "memory_type": "media_summary",
                "title": "旧标题",
                "content": "旧内容",
                "source_type": "bilibili_video_summary",
                "confidence": 0.6,
                "importance": "medium",
                "status": "validated",
            },
            target_collection="financial_knowledge",
        )
        updated = write_memory_and_enqueue(
            {
                "memory_type": "media_summary",
                "title": "新标题",
                "content": "新内容",
                "source_type": "bilibili_video_summary",
                "confidence": 0.9,
                "importance": "high",
                "status": "validated",
            },
            target_collection="financial_knowledge",
            existing_memory_id=created["memory_id"],
        )
        record = repo.get(created["memory_id"])
        assert updated["memory_id"] == created["memory_id"]
        assert record is not None
        assert record.title == "新标题"
        assert record.content == "新内容"
        assert float(record.confidence) == 0.9
        assert record.importance == "high"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
