from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    imports: set[str] = set()
    for source in path.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
    return imports


def test_subsystem_http_clients_do_not_import_legacy_engines():
    imports = _imports(ROOT / "clients")
    assert not {
        name
        for name in imports
        if name == "engines" or name.startswith("engines.") or name == "financial_agent" or name.startswith("financial_agent.")
    }


def test_factor_mcp_adapter_does_not_import_factor_engine():
    source = ROOT / "mcp_servers" / "factor_mining_server.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    direct = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            direct.add(node.module)
        elif isinstance(node, ast.Import):
            direct.update(alias.name for alias in node.names)
    assert not {name for name in direct if name == "engines.factor" or name.startswith("engines.factor")}
