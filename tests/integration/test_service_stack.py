import os

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
