from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _imports_below(package: str, exclude: set[str] | None = None) -> set[str]:
    imports: set[str] = set()
    for source in (ROOT / package).rglob("*.py"):
        if "__pycache__" in source.parts:
            continue
        if source.name in (exclude or set()):
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
    return imports


def test_runtime_packages_do_not_import_historical_packages():
    imports = set().union(*(_imports_below(package) for package in ("agent", "app", "engines", "storage", "workers")))
    assert not {name for name in imports if name == "architect" or name.startswith("architect.") or name == "artitect" or name.startswith("artitect.")}


def test_engine_layer_does_not_depend_on_fastapi_route_layer():
    imports = _imports_below("engines")
    assert not {name for name in imports if name == "fastapi" or name.startswith("fastapi.") or name == "app.api" or name.startswith("app.api.")}


def test_agent_layer_does_not_depend_on_fastapi_route_layer():
    imports = _imports_below("agent")
    assert not {name for name in imports if name == "app.api" or name.startswith("app.api.")}


def test_storage_does_not_depend_on_agent_orchestration():
    imports = _imports_below("storage")
    assert not {name for name in imports if name == "agent" or name.startswith("agent.")}


def test_workers_do_not_depend_on_http_routes():
    # These two files are separately deployed ASGI adapters; all worker compute modules
    # remain free of HTTP-framework dependencies.
    imports = _imports_below("workers", exclude={"embedding_api.py", "reranker_api.py"})
    assert not {name for name in imports if name == "fastapi" or name.startswith("fastapi.") or name == "app.api" or name.startswith("app.api.")}


def test_legacy_financial_agent_does_not_depend_on_runtime_orchestration():
    imports = _imports_below("financial_agent")
    assert not {name for name in imports if name == "agent" or name.startswith("agent.")}
