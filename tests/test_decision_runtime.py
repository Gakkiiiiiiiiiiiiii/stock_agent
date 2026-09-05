"""P0 A-07：DecisionRuntime 主流程集成测试。"""
from __future__ import annotations

from typing import ClassVar

from app.decision_runtime import DecisionRuntime
from storage.repositories.research_repository import DecisionSnapshotRepository


class _StubClaudeResult:
    selected_skill = "a-share-technical-analysis"
    selection_reason = "forced-skill"
    tool_calls: ClassVar[list] = []
    trace: ClassVar[dict] = {"steps": []}
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


def _runtime(configured: bool = False) -> DecisionRuntime:
    return DecisionRuntime(claude_agent=_StubClaudeAgent(configured=configured), fallback=_StubFallback())


def test_primary_agent_legacy_path_is_narrative_only(isolated_database):
    runtime = _runtime(configured=True)

    result = runtime.analyze_stock("600000.SH")

    assert result["runtime_mode"] == "PRIMARY_AGENT"
    assert result["actionable"] is False
    assert "decision_id" not in result
    assert DecisionSnapshotRepository().get_for_decision("missing") is None


def test_deterministic_legacy_path_is_narrative_only(isolated_database):
    runtime = _runtime(configured=False)

    result = runtime.analyze_stock("600000.SH")

    assert result["runtime_mode"] == "DETERMINISTIC_FALLBACK"
    assert result["actionable"] is False
    assert "decision_id" not in result


def test_all_public_entries_use_same_runtime(isolated_database, monkeypatch):
    runtime = _runtime(configured=False)
    stock = runtime.analyze_stock("600000.SH")
    theme = runtime.analyze_theme("黄金")
    scan = runtime.daily_scan()
    run = runtime.run("分析市场")

    # Legacy public entries are narrative-only and cannot enter governance.
    for item in (stock, theme, scan, run):
        assert item["actionable"] is False
        assert "decision_id" not in item


def test_runtime_mode_is_returned_without_legacy_snapshot(isolated_database):
    runtime = _runtime(configured=True)

    result = runtime.daily_scan()

    assert result["runtime_mode"] == "PRIMARY_AGENT"
    assert "decision_id" not in result
