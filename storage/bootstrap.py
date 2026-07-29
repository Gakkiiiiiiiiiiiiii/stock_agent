from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from storage.models import chat  # noqa: F401
from storage.models import content  # noqa: F401
from storage.models import knowledge  # noqa: F401
from storage.db import Base, get_engine
from storage.models import vector  # noqa: F401
from financial_agent.utils import project_root


def create_all() -> None:
    Base.metadata.create_all(bind=get_engine())
    apply_sql_migrations()


def apply_sql_migrations() -> None:
    engine = get_engine()
    migrations_dir = project_root() / "storage" / "migrations"
    if not migrations_dir.exists():
        return
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migration (version VARCHAR(128) PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        applied = {row[0] for row in conn.exec_driver_sql("SELECT version FROM schema_migration").fetchall()}
        is_sqlite = engine.url.get_backend_name().startswith("sqlite")
        paths = sorted(Path(migrations_dir).glob("*.sql"))
        if is_sqlite:
            paths = [path for path in paths if path.name >= "006_"]
        for path in paths:
            if path.name in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            if not is_sqlite and "AUTOINCREMENT" in sql.upper():
                conn.execute(text("INSERT INTO schema_migration(version) VALUES (:version)"), {"version": path.name})
                continue
            for statement in [part.strip() for part in sql.split(";") if part.strip()]:
                conn.exec_driver_sql(statement)
            conn.execute(text("INSERT INTO schema_migration(version) VALUES (:version)"), {"version": path.name})
