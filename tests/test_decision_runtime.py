"""P0 A-07：DecisionRuntime 主流程集成测试。"""
from __future__ import annotations

from app.decision_runtime import DecisionRuntime
from storage.repositories.research_repository import DecisionSnapshotRepository


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


def _runtime(configured: bool = False) -> DecisionRuntime:
    return DecisionRuntime(claude_agent=_StubClaudeAgent(configured=configured), fallback=_StubFallback())


def test_primary_agent_path_persists_decision_snapshot(isolated_database):
    runtime = _runtime(configured=True)

    result = runtime.analyze_stock("600000.SH")

    assert result["runtime_mode"] == "PRIMARY_AGENT"
    assert result["decision_id"] and result["decision_snapshot_id"]
    snapshot = DecisionSnapshotRepository().get_for_decision(result["decision_id"])
    assert snapshot is not None
    assert snapshot.schema_version == "decision.snapshot.v2"
    assert snapshot.runtime["runtime_mode"] == "PRIMARY_AGENT"
    assert snapshot.runtime["fallback_used"] is False
    assert snapshot.proposal and snapshot.policy


def test_deterministic_fallback_persists_decision_snapshot(isolated_database):
    runtime = _runtime(configured=False)

    result = runtime.analyze_stock("600000.SH")

    assert result["runtime_mode"] == "DETERMINISTIC_FALLBACK"
    snapshot = DecisionSnapshotRepository().get_for_decision(result["decision_id"])
    assert snapshot is not None
    assert snapshot.runtime["runtime_mode"] == "DETERMINISTIC_FALLBACK"
    assert snapshot.runtime["fallback_used"] is True
    assert snapshot.runtime["fallback_reason"] == "MODEL_UNAVAILABLE"


def test_all_public_entries_use_same_runtime(isolated_database, monkeypatch):
    runtime = _runtime(configured=False)
    governed: list[dict] = []
    original = DecisionRuntime._govern

    def _spy(self, payload):
        governed.append(payload)
        return original(self, payload)

    monkeypatch.setattr(DecisionRuntime, "_govern", _spy)

    stock = runtime.analyze_stock("600000.SH")
    theme = runtime.analyze_theme("黄金")
    scan = runtime.daily_scan()
    run = runtime.run("分析市场")

    # analyze_stock / analyze_theme / daily_scan / actionable run_agent 全部走同一 runtime 治理链。
    for item in (stock, theme, scan, run):
        assert item["decision_id"] and item["decision_snapshot_id"]
        assert item["runtime_mode"] == "DETERMINISTIC_FALLBACK"
    assert len(governed) == 4


def test_runtime_mode_is_persisted_into_snapshot(isolated_database):
    runtime = _runtime(configured=True)

    result = runtime.daily_scan()

    snapshot = DecisionSnapshotRepository().get_for_decision(result["decision_id"])
    assert snapshot.runtime["runtime_mode"] == result["runtime_mode"] == "PRIMARY_AGENT"
