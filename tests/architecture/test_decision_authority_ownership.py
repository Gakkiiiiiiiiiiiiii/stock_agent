"""D1 ownership guardrails for the Investment Decision Authority boundary."""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

from app.api import app
from app import dependencies
from app.tool_registry import ClaudeToolRegistry
from app.tool_policy import PermissionLevel
from fastapi.routing import APIRoute

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_TOOL_NAMES = {
    "evaluate_factor",
    "scan_alpha_factors",
    "upsert_theme_logic",
    "upsert_knowledge",
    "place_order",
    "submit_order",
    "cancel_order",
    "save_investment_decision",
    "record_decision_outcome",
    "review_investment_decision",
}
FORBIDDEN_PREFIXES = ("mine_", "ingest_", "train_")
FORBIDDEN_IMPORT_PARTS = (
    "engines.execution",
    "qmt_bridge_client",
    "engines.market.data_provider",
    "engines.market.feature_service",
    "mcp_servers.market_data_server",
    "mcp_servers.technical_factor_server",
    "mcp_servers.factor_mining_server",
    "mcp_servers.market_regime_server",
    "mcp_servers.retrieval_server",
    "mcp_servers.decision_server",
    "engines.retrieval",
)


def test_decision_registry_exposes_no_external_production_or_order_tools() -> None:
    names = {item["name"] for item in ClaudeToolRegistry().anthropic_tools()}
    assert not {name for name in names if name in FORBIDDEN_TOOL_NAMES or name.startswith(FORBIDDEN_PREFIXES)}
    assert "get_market_snapshot" in names
    assert "get_factor_evidence" in names
    assert {"get_factor_scores", "get_subject_state", "get_decision_history"} <= names
    assert "search_content_knowledge" in names
    assert "get_kline" not in names
    assert not names.intersection({"calc_profile_indicators", "evaluate_technical_rules", "scan_technical_rules"})
    assert not names.intersection({"save_investment_decision", "record_decision_outcome", "review_investment_decision", "search_memory", "search_strategy_memory"})


def test_tool_permissions_are_limited_to_decision_authority_levels() -> None:
    assert {item.value for item in PermissionLevel} == {"READ", "COMPUTE", "INTERNAL_WRITE"}
    registry = ClaudeToolRegistry()
    assert set(registry._policies).issubset(set(registry._tools))


def test_stock_agent_route_table_has_no_mutating_external_surfaces() -> None:
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert not any("execution" in path for path in paths)
    assert not any(token in path for path in paths for token in ("/ingest", "/factors/mine", "/cancel", "/knowledge/theme"))
    assert not any("/proposals" in path for path in paths)
    assert not any(route.path.endswith("/admin/skills/{slug}") and "PUT" in route.methods for route in app.routes if isinstance(route, APIRoute))


def test_main_path_does_not_import_local_fact_producers_or_execution() -> None:
    roots = (ROOT / "app", ROOT / "agent")
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imported = [node.module or ""]
                else:
                    continue
                assert not any(any(part in item for part in FORBIDDEN_IMPORT_PARTS) for item in imported), f"forbidden main-path import in {path}: {imported}"


def test_clients_do_not_depend_on_engine_implementations() -> None:
    for path in (ROOT / "clients").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                continue
            assert not any(item.startswith("engines") for item in imported), f"client imports engine: {path}: {imported}"


def test_default_deployment_disables_legacy_fact_and_execution_workloads() -> None:
    values = (ROOT / "deploy" / "helm" / "stock-agent" / "values.yaml").read_text(encoding="utf-8")
    assert "market-data: {enabled: false}" in values
    assert "execution: {enabled: false}" in values
    assert "retrieval: {enabled: false}" in values
    assert "market-feature-snapshot" not in values
    assert "vector-reconciliation" not in values
    assert "services.execution_api" not in values
    assert "services.market_data_api" not in values
    assert "LIVE" not in values and "QMT" not in values


def test_legacy_retrieval_and_regime_producers_are_not_main_route_imports() -> None:
    for filename in (ROOT / "app" / "routers" / "regime.py", ROOT / "app" / "routers" / "retrieval.py"):
        text = filename.read_text(encoding="utf-8")
        assert "mcp_servers.market_regime_server" not in text
        assert "mcp_servers.retrieval_server" not in text
        assert "MarketFeatureService" not in text


def test_production_clock_is_quant_calendar_backed(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []
    original_quant_client = dependencies.quant_client
    from engines.market.trading_clock import get_default_clock

    initial_clock = get_default_clock()

    class _Quant:
        def get_trading_calendar(self, start: str, end: str, *, market_code: str = "CN_A") -> dict:
            calls.append((start, end, market_code))
            return {"sessions": [{"date": "2026-08-24", "is_open": True}]}

    monkeypatch.setattr(dependencies, "quant_client", _Quant())
    dependencies.configure_trading_clock()

    clock = get_default_clock()
    assert clock.degraded is False
    assert clock.current_trading_session(value=__import__("datetime").datetime(2026, 8, 24)) == date(2026, 8, 24)
    assert calls and calls[0][2] == "CN_A"

    # Offline is explicit and visibly degraded; it is not a production fallback.
    dependencies.configure_trading_clock(offline=True)
    assert get_default_clock().degraded is True
    dependencies.quant_client = original_quant_client
    from engines.market.trading_clock import configure_default_clock

    configure_default_clock(initial_clock)


def test_orchestrator_captures_the_shared_production_clock() -> None:
    from engines.market.trading_clock import get_default_clock

    shared = get_default_clock()
    assert dependencies.orchestrator.runtime.clock is shared
    assert dependencies.orchestrator.runtime.outcome_provider.clock is shared
    assert dependencies.orchestrator.runtime.fallback.clock is shared
    assert dependencies.orchestrator.runtime.claude_agent.clock is shared
    assert shared.degraded is False


def test_runtime_evidence_gateway_is_wired_to_remote_read_clients() -> None:
    runtime = dependencies.orchestrator.runtime
    gateway = runtime.evidence_gateway
    assert gateway.quant_client is dependencies.quant_client
    assert gateway.factor_client is dependencies.factor_client
    assert gateway.content_client is dependencies.content_client
    assert gateway.clock is runtime.clock
