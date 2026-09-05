from __future__ import annotations

import pytest
from sqlalchemy.orm import close_all_sessions

from engines.market.trading_clock import (
    TradingClock,
    configure_default_clock,
    get_default_clock,
)
from storage.bootstrap import create_all
from storage.db import SessionLocal, get_engine
from storage.repositories.job_repository import JobTaskRepository


@pytest.fixture(autouse=True)
def restore_global_session_factory(monkeypatch):
    """Keep temporary bindings and the default clock isolated per test."""
    original_bind = SessionLocal.kw.get("bind")
    original_clock = get_default_clock()
    configure_default_clock(TradingClock())
    monkeypatch.setenv("STOCK_AGENT_OFFLINE_MODE", "1")
    try:
        yield
    finally:
        close_all_sessions()
        SessionLocal.configure(bind=original_bind)
        configure_default_clock(original_clock)


@pytest.fixture
def isolated_database(monkeypatch, tmp_path):
    previous_clock = get_default_clock()
    configure_default_clock(TradingClock())
    # Database-isolated tests must not initialize remote calendar clients.
    monkeypatch.setenv("STOCK_AGENT_OFFLINE_MODE", "1")
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
    configure_default_clock(previous_clock)
