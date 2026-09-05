from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

Base = declarative_base()


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = os.getenv("DATABASE_URL", "sqlite:///./financial_agent.db")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("sqlite"):
        # Formal finalization uses BEGIN IMMEDIATE on SQLite test/smoke
        # databases; wait for the current owner instead of failing on a
        # transient writer lock.
        connect_args: dict[str, object] = {"check_same_thread": False, "timeout": 30}
    elif url.startswith("postgresql+psycopg"):
        connect_args = {"connect_timeout": 5}
    else:
        connect_args = {}
    return create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)


SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False, future=True)
_BOUND_SESSION: ContextVar[Session | None] = ContextVar("bound_storage_session", default=None)


@contextmanager
def bind_session(session: Session) -> Iterator[Session]:
    """Make nested repository calls participate in one request transaction."""
    marker = _BOUND_SESSION.set(session)
    try:
        yield session
    finally:
        _BOUND_SESSION.reset(marker)


@contextmanager
def session_scope() -> Iterator[Session]:
    bound = _BOUND_SESSION.get()
    if bound is not None:
        yield bound
        return
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
