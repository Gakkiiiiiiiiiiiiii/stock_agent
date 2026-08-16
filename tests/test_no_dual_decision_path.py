"""P0 A-06：禁止双主链路（单一决策链路治理）。"""
from __future__ import annotations

import ast
from pathlib import Path

from app.agent_orchestrator import AgentOrchestrator
from app.decision_runtime import DecisionRuntime


class _StubClaudeResult:
    selected_skill = "a-share-technical-analysis"
    selection_reason = "forced-skill"
    tool_calls = []
    trace = {"steps": []}
    report = "stub-report"


class _StubClaudeAgent:
    def __init__(self, configured: bool = True) -> None:
        self._configured = configured

    def configured(self) -> bool:
        return self._configured

    def run(self, user_query=None, context=None, force_skill=None, emit=None):
        return _StubClaudeResult()


class _StubFallback:
    def analyze_stock(self, symbol, as_of=None, patterns=None):
        return {"symbol": symbol, "orchestration": "local-fallback", "summary": "stub"}

    def analyze_theme(self, theme_name):
        return {"theme_name": theme_name, "orchestration": "local-fallback"}

    def daily_scan(self, scan_date=None, mode="after_close"):
        return {"date": str(scan_date), "mode": mode, "orchestration": "local-fallback"}


def _orchestrator() -> AgentOrchestrator:
    runtime = DecisionRuntime(claude_agent=_StubClaudeAgent(configured=False), fallback=_StubFallback())
    return AgentOrchestrator(runtime=runtime)


def test_facade_delegates_to_runtime(monkeypatch):
    runtime = DecisionRuntime(claude_agent=_StubClaudeAgent(configured=False), fallback=_StubFallback())
    calls: list[tuple] = []

    def _spy(self, symbol, as_of=None, patterns=None):
        calls.append((symbol, as_of, patterns))
        return {"delegated": True}

    monkeypatch.setattr(DecisionRuntime, "analyze_stock", _spy)
    result = AgentOrchestrator(runtime=runtime).analyze_stock("600000.SH", patterns=["breakout"])

    assert result == {"delegated": True}
    assert calls == [("600000.SH", None, ["breakout"])], "facade 必须只委托 DecisionRuntime 一次"


def test_public_facade_has_no_direct_execution_calls():
    """AST 级断言：public facade 不允许直接调用 ClaudeAgent.run / fallback 决策方法。"""
    source = Path("app/agent_orchestrator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_decision_calls = {"analyze_stock", "analyze_theme", "daily_scan"}

    def dotted(node) -> str:
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) or func.name.startswith("_"):
            continue
        for node in ast.walk(func):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            base = dotted(node.func.value)
            attr = node.func.attr
            assert not (attr == "run" and "claude_agent" in base), f"public {func.name} 直接调用 ClaudeAgent.run"
            assert not ("fallback" in base and attr in forbidden_decision_calls), (
                f"public {func.name} 直接调用 LocalFallbackOrchestrator.{attr}"
            )


def test_actionable_output_carries_decision_identity(isolated_database):
    result = _orchestrator().analyze_stock("600000.SH")

    assert result["decision_id"], "actionable 输出必须携带 decision_id"
    assert result["decision_snapshot_id"], "actionable 输出必须携带 decision_snapshot_id"
    assert result["runtime_mode"]
    assert result["final_decision"]


def test_fallback_is_also_governed(isolated_database):
    result = _orchestrator().analyze_stock("600000.SH")

    assert result["runtime_mode"] == "DETERMINISTIC_FALLBACK"
    assert result["decision_snapshot_id"] is not None
    assert result["policy"], "fallback 决策同样必须经过 PolicyEngine 治理"
