from __future__ import annotations

import pytest
from sqlalchemy.orm import close_all_sessions

from storage.bootstrap import create_all
from storage.db import SessionLocal, get_engine
from storage.repositories.job_repository import JobTaskRepository


@pytest.fixture(autouse=True)
def restore_global_session_factory():
    """Keep tests that bind temporary SQLite engines from leaking into later tests."""
    original_bind = SessionLocal.kw.get("bind")
    try:
        yield
    finally:
        close_all_sessions()
        SessionLocal.configure(bind=original_bind)


@pytest.fixture
def isolated_database(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    get_engine.cache_clear()
    engine = get_engine()
    SessionLocal.configure(bind=engine)
    JobTaskRepository._schema_ready = False
    create_all()
    yield engine
    engine.dispose()
    JobTaskRepository._schema_ready = False
    get_engine.cache_clear()
