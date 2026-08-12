from pathlib import Path

from sqlalchemy import create_engine, text

from financial_agent.utils import project_root
from storage import bootstrap


def test_sqlite_agent_migrations_apply_once(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'migrations.db'}", future=True)
    monkeypatch.setattr(bootstrap, "get_engine", lambda: engine)
    bootstrap.apply_sql_migrations()
    bootstrap.apply_sql_migrations()
    with engine.connect() as conn:
        versions = conn.execute(text("SELECT version FROM schema_migration ORDER BY version")).scalars().all()
    assert versions == sorted(set(versions))
    assert "010_agent_learning_loop.sql" in versions
    assert not any("knowledge" in version or "content" in version or "video" in version for version in versions)


def test_postgres_agent_migration_selection_uses_backend_variants():
    paths = bootstrap._migration_paths_for_backend(project_root() / "storage" / "migrations", "postgres")
    assert not any("knowledge" in path.name or "content" in path.name or "video" in path.name for path in paths)


def test_postgres_autoincrement_migration_is_rejected_when_no_variant(tmp_path):
    path = Path(tmp_path / "010_bad.sql")
    sql = "CREATE TABLE demo (id INTEGER PRIMARY KEY AUTOINCREMENT)"
    try:
        bootstrap._ensure_migration_sql_compatible(path, sql, "postgres")
    except RuntimeError as exc:
        assert "AUTOINCREMENT" in str(exc)
    else:
        raise AssertionError("expected SQLite AUTOINCREMENT migration to be rejected for postgres")
