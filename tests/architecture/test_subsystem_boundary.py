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
        if name in {"engines", "financial_agent"} or name.startswith(("engines.", "financial_agent."))
    }


def test_mcp_servers_package_is_removed_and_tools_use_application_ports():
    assert not list((ROOT / "mcp_servers").glob("*.py"))
    tool_sources = list((ROOT / "app" / "tools").rglob("*.py"))
    assert all("mcp_servers" not in path.read_text(encoding="utf-8") for path in tool_sources)
    portfolio_tool = (ROOT / "app" / "tools" / "portfolio_tools.py").read_text(encoding="utf-8")
    assert "app.ports.portfolio" in portfolio_tool
    assert "app.adapters.local.portfolio" in portfolio_tool


def test_retired_execution_and_local_market_producers_are_absent():
    retired = (
        ROOT / "engines" / "execution",
        ROOT / "services" / "execution_api.py",
        ROOT / "contracts" / "execution.py",
        ROOT / "services" / "market_data_api.py",
        ROOT / "workers" / "market_stream_worker.py",
        ROOT / "engines" / "market" / "data_provider.py",
        ROOT / "engines" / "market" / "feature_service.py",
        ROOT / "engines" / "market" / "qmt_bridge_client.py",
    )
    assert all(not path.exists() for path in retired)
    production = (ROOT / "app", ROOT / "services", ROOT / "workers", ROOT / "scripts")
    forbidden = ("engines.execution", "services.execution_api", "services.market_data_api", "engines.market.data_provider", "engines.market.feature_service", "engines.market.qmt_bridge_client")
    for root in production:
        for path in root.rglob("*.py"):
            assert not any(token in path.read_text(encoding="utf-8") for token in forbidden), path


def test_formal_runtime_paths_have_no_mcp_imports():
    for dirname in ("app", "agent", "services", "workers"):
        for path in (ROOT / dirname).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module)
            assert not any(item == "mcp_servers" or item.startswith("mcp_servers.") for item in imports), path


def test_portfolio_application_depends_only_on_ports_and_domain():
    """Portfolio application code cannot reach concrete adapters or storage."""
    for path in (ROOT / "app" / "application" / "portfolio").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        assert not any(item == "engines" or item.startswith("engines.") for item in imports), path
        assert not any(item == "storage" or item.startswith("storage.") for item in imports), path
        assert not any(item.startswith("app.adapters") for item in imports), path


def test_retrieval_adapter_is_read_only():
    from app.adapters.local.retrieval import LocalRetrievalAdapter

    adapter = LocalRetrievalAdapter()
    assert hasattr(adapter, "retrieve_relevant_context")
    assert not any(hasattr(adapter, name) for name in ("index_memory_to_qdrant", "enqueue", "write"))


def test_decision_runtime_is_a_facade_over_phase_components():
    """Runtime may coordinate collaborators, but not own concrete persistence."""
    source = (ROOT / "app" / "decision_runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(ROOT / "app" / "decision_runtime.py"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    assert not any(item.startswith("storage.repositories") for item in imports)
    assert not any(item.startswith("engines.") for item in imports)
    for component in ("bundle_freezer", "specialist_runner", "formal_calculator", "snapshot_builder"):
        assert f"self.{component} =" in source


def test_application_layers_have_no_dynamic_or_concrete_infrastructure_imports():
    """Application components must receive infrastructure through ports."""
    roots = (
        ROOT / "app" / "application" / "decision",
        ROOT / "app" / "application" / "analysis",
        ROOT / "app" / "application" / "portfolio",
    )
    forbidden_prefixes = ("app.adapters", "engines", "storage")
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    assert node.func.id != "__import__", path
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                    assert not any(name.startswith(forbidden_prefixes) for name in names), path
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith(forbidden_prefixes), path
            assert "mypy: ignore" not in path.read_text(encoding="utf-8"), path


_CODE_DIRS = (
    "agent",
    "app",
    "clients",
    "contracts",
    "engines",
    "financial_agent",
    "services",
    "storage",
    "workers",
)


def test_agent_integrates_other_repos_only_via_http():
    # §6.3：agent 对 quant/factor/content 只能走 HTTP 契约，
    # 禁止 import 其他仓库的 Python 实现（quant_demo / stock_factor / stock_content）。
    imports: set[str] = set()
    for dirname in _CODE_DIRS:
        path = ROOT / dirname
        if path.exists():
            imports |= _imports(path)
    forbidden = {
        name
        for name in imports
        if name.startswith(("quant_demo", "stock_factor", "stock_content"))
    }
    assert not forbidden
