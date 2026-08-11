from __future__ import annotations

import time

from app.admin_service import AdminContentService
from app.agent_orchestrator import AgentOrchestrator
from app.chat_history_service import ChatHistoryService
from engines.content.video_ingest_service import VideoIngestService
from engines.retrieval.qdrant_client import FinancialQdrantClient
from storage.bootstrap import create_all
from storage.repositories.knowledge_repository import KnowledgeRepository


def init_application() -> None:
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
    try:
        FinancialQdrantClient().ensure_collections()
    except Exception:
        # Local tests or first boot without Qdrant should still allow the API to start.
        pass


# Shared service singletons (P0-05 / §28)：app.api 与各 app.routers 统一从这里取。
# 注意：routers 必须在调用时通过 ``app.dependencies`` 模块属性读取这些对象，
# 以便测试可以整体替换（monkeypatch.setattr("app.dependencies.<name>", fake)）。
orchestrator = AgentOrchestrator()
admin_service = AdminContentService()
chat_history_service = ChatHistoryService()
content_ingest_service = VideoIngestService()
knowledge_repository = KnowledgeRepository()
