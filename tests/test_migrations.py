from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from financial_agent.utils import project_root
from storage import bootstrap
from storage.db import Base


def test_sqlite_migrations_apply_once(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'migrations.db'}", future=True)
    monkeypatch.setattr(bootstrap, "get_engine", lambda: engine)

    bootstrap.apply_sql_migrations()
    bootstrap.apply_sql_migrations()

    with engine.connect() as conn:
        versions = conn.execute(text("SELECT version FROM schema_migration ORDER BY version")).scalars().all()
    assert versions == sorted(set(versions))
    assert "007_video_knowledge_v3_schema.sql" in versions
    assert "008_knowledge_lifecycle_audit.sql" in versions
    assert "009_knowledge_extraction_quality_metrics.sql" in versions


def test_knowledge_extraction_run_metrics_column_exists_after_migration(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'metrics_column.db'}", future=True)
    monkeypatch.setattr(bootstrap, "get_engine", lambda: engine)
    Base.metadata.create_all(bind=engine)

    bootstrap.apply_sql_migrations()

    columns = {column["name"] for column in inspect(engine).get_columns("knowledge_extraction_run")}
    assert "metrics_json" in columns


def test_postgres_migration_selection_uses_backend_variants():
    paths = bootstrap._migration_paths_for_backend(project_root() / "storage" / "migrations", "postgres")
    selected = {path.name: path for path in paths}

    for name in {
        "005_financial_event_schema.sql",
        "007_video_knowledge_v3_schema.sql",
        "008_knowledge_lifecycle_audit.sql",
        "009_knowledge_extraction_quality_metrics.sql",
    }:
        assert selected[name].parts[-2:] == ("postgres", name)
        sql = selected[name].read_text(encoding="utf-8")
        assert "AUTOINCREMENT" not in sql.upper()


def test_postgres_autoincrement_migration_is_rejected_when_no_variant(tmp_path):
    path = Path(tmp_path / "010_bad.sql")
    sql = "CREATE TABLE demo (id INTEGER PRIMARY KEY AUTOINCREMENT)"

    try:
        bootstrap._ensure_migration_sql_compatible(path, sql, "postgres")
    except RuntimeError as exc:
        assert "AUTOINCREMENT" in str(exc)
    else:
        raise AssertionError("expected SQLite AUTOINCREMENT migration to be rejected for postgres")
