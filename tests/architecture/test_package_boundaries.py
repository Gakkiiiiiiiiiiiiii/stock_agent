from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _imports_below(package: str) -> set[str]:
    imports: set[str] = set()
    for source in (ROOT / package).rglob("*.py"):
        if "__pycache__" in source.parts:
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
