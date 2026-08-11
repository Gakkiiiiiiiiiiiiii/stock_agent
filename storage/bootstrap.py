from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from storage.models import chat  # noqa: F401
from storage.models import content  # noqa: F401
from storage.models import knowledge  # noqa: F401
from storage.models import market_feature  # noqa: F401
from storage.models import p2  # noqa: F401
from storage.db import Base, get_engine
from storage.models import vector  # noqa: F401
from storage.models import research  # noqa: F401
from financial_agent.utils import project_root


def create_all() -> None:
    Base.metadata.create_all(bind=get_engine())
    apply_sql_migrations()


def apply_sql_migrations() -> None:
    engine = get_engine()
    migrations_dir = project_root() / "storage" / "migrations"
    if not migrations_dir.exists():
        return
    backend = _migration_backend(engine.url.get_backend_name())
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migration (version VARCHAR(128) PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        applied = {row[0] for row in conn.exec_driver_sql("SELECT version FROM schema_migration").fetchall()}
        paths = _migration_paths_for_backend(migrations_dir, backend)
        for path in paths:
            if path.name in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            _ensure_migration_sql_compatible(path, sql, backend)
            for statement in [part.strip() for part in sql.split(";") if part.strip()]:
                try:
                    conn.exec_driver_sql(statement)
                except Exception as exc:  # noqa: BLE001
                    message = str(exc).lower()
                    if "duplicate column" in message or ("already exists" in message and "column" in message):
                        continue
                    raise
            conn.execute(text("INSERT INTO schema_migration(version) VALUES (:version)"), {"version": path.name})


def _migration_backend(backend_name: str) -> str:
    if backend_name.startswith("sqlite"):
        return "sqlite"
    if backend_name.startswith("postgresql"):
        return "postgres"
    return backend_name


def _migration_paths_for_backend(migrations_dir: Path, backend: str) -> list[Path]:
    root_paths = sorted(Path(migrations_dir).glob("*.sql"))
    backend_dir = migrations_dir / backend
    backend_paths = {path.name: path for path in sorted(backend_dir.glob("*.sql"))} if backend_dir.exists() else {}
    selected: list[Path] = []
    root_names: set[str] = set()
    for path in root_paths:
        root_names.add(path.name)
        if backend == "sqlite" and path.name < "006_":
            continue
        selected.append(backend_paths.get(path.name, path))
    for name, path in backend_paths.items():
        if name not in root_names:
            selected.append(path)
    return sorted(selected, key=lambda item: item.name)


def _ensure_migration_sql_compatible(path: Path, sql: str, backend: str) -> None:
    if backend != "sqlite" and "AUTOINCREMENT" in sql.upper():
        raise RuntimeError(
            f"migration {path.name} contains SQLite AUTOINCREMENT but no {backend} variant was selected"
        )
