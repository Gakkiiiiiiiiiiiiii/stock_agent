import os
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text


pytestmark = pytest.mark.integration


def test_postgres_connection_and_transaction_roundtrip():
    if not os.getenv("DATABASE_URL", "").startswith("postgresql://"):
        pytest.skip("DATABASE_URL does not point to postgres")
    from storage.db import session_scope

    with session_scope() as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1
        session.execute(text("CREATE TEMP TABLE codex_integration_probe(value INTEGER) ON COMMIT DROP"))
        session.execute(text("INSERT INTO codex_integration_probe(value) VALUES (7)"))
        assert session.execute(text("SELECT value FROM codex_integration_probe")).scalar_one() == 7


def test_redis_ping():
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL is not configured")
    import redis

    client = redis.Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
    assert client.ping() is True


def test_retrieval_pipeline_indexes_hydrates_and_reranks_real_services():
    """Postgres → vector task/worker → embedding/Qdrant → rerank → hydration."""
    if os.getenv("RUN_FULL_STACK_E2E") != "1":
        pytest.skip("set RUN_FULL_STACK_E2E=1 to run the mutating full retrieval E2E")
    if not os.getenv("DATABASE_URL", "").startswith("postgresql://"):
        pytest.skip("DATABASE_URL does not point to postgres")
    from engines.retrieval.qdrant_client import FinancialQdrantClient
    from mcp_servers.retrieval_server import retrieve_relevant_context
    from storage.repositories.vector_repository import MemoryRepository, VectorMappingRepository, VectorTaskRepository

    marker = f"full-stack-{uuid4()}"
    memory = MemoryRepository().create(
        memory_type="full_stack_e2e", title=marker,
        content=f"{marker} 检索链路验证：Qdrant、重排和数据库回填均应返回本记录。",
        source_type="integration_test", source_date=datetime.now(UTC), status="validated",
    )
    VectorTaskRepository().enqueue("memory_record", memory.id, "financial_memory_v2_bge_m3")
    deadline = time.monotonic() + 150
    mappings = []
    while time.monotonic() < deadline:
        mappings = VectorMappingRepository().list_for_record("memory_record", memory.id)
        if mappings and all(item.index_status == "indexed" for item in mappings):
            break
        time.sleep(2)
    assert mappings, "vector_worker did not index the newly created record"
    # This verifies the deployed Qdrant collection is readable before the
    # retrieval call (the latter additionally covers embedder/reranker/hydrator).
    assert FinancialQdrantClient().search("financial_memory_v2_bge_m3", [0.0] * 1024, 1) is not None
    result = retrieve_relevant_context(marker, task_type="general_research", top_k=5)
    context = next((item for item in result["contexts"] if (item.get("record") or {}).get("id") == memory.id), None)
    assert context is not None
    assert context["record"]["id"] == memory.id
    assert context["content"] == memory.content
    assert context["source"] == "full_stack_e2e"
    assert context["version"] == "v1"
