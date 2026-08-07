from __future__ import annotations

import json
from datetime import date

from agent.executor import SkillExecutor
from app.skill_contract import SkillExecutionContract, SkillOutputContract
from app.skill_loader import SkillDefinition
from engines.decision.decision_service import DecisionService
from engines.memory.service import MemoryService
from engines.regime.regime_state_machine import PersistentRegimeStateMachine
from storage.bootstrap import create_all
from storage.db import SessionLocal, get_engine
from storage.repositories.vector_repository import MemoryRepository


def configure_test_database(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'learning_loop.db'}")
    get_engine.cache_clear()
    SessionLocal.configure(bind=get_engine())
    create_all()


class FakeClient:
    def __init__(self, responses):
        self.responses = responses

    def create_chat_completion(self, **_kwargs):
        return self.responses.pop(0)


class FakeRegistry:
    def openai_tools(self):
        return []

    def describe_tool(self, name):
        return name

    def execute(self, name, _payload):
        return {"snapshot": {"as_of": "2026-08-07T09:30:00+08:00"}} if name == "get_market_snapshot" else {"ok": True}


def test_skill_contract_requires_tools_before_final():
    client = FakeClient([
        {"choices": [{"message": {"content": "### 结论\n过早结束"}}]},
        {"choices": [{"message": {"tool_calls": [{"id": "1", "function": {"name": "get_market_snapshot", "arguments": json.dumps({})}}]}}]},
        {"choices": [{"message": {"content": "### 结论\n完成"}}]},
    ])
    skill = SkillDefinition(
        slug="contract-test", name="contract-test", description="", content="",
        execution=SkillExecutionContract(required_tools=["get_market_snapshot"], require_fresh_market_data=True),
        output=SkillOutputContract(required_sections=["结论"]),
    )
    report, calls, trace, state = SkillExecutor(client, FakeRegistry()).run(skill, "test", {}, "system")
    assert report.endswith("完成")
    assert calls[0]["name"] == "get_market_snapshot"
    assert state.contract_violations == []
    assert any(step["type"] == "contract_violation" for step in trace)


def test_persistent_regime_uses_confirmation_days(monkeypatch, tmp_path):
    configure_test_database(monkeypatch, tmp_path)
    state_machine = PersistentRegimeStateMachine()
    state_machine.advance("CN_A", "rotation_market", date(2026, 8, 1))
    first = state_machine.advance("CN_A", "range_market", date(2026, 8, 2))
    second = state_machine.advance("CN_A", "range_market", date(2026, 8, 3))
    third = state_machine.advance("CN_A", "range_market", date(2026, 8, 4))
    assert first["switch_status"] == "watch_switch"
    assert second["candidate_days"] == 2
    assert third["confirmed_regime"] == "range_market"
    assert third["switch_status"] == "confirmed_switch"


def test_memory_merge_and_decision_review_feed_long_term_memory(monkeypatch, tmp_path):
    configure_test_database(monkeypatch, tmp_path)
    memory = MemoryService()
    first = memory.ingest("decision_review", "decision-1", "轮动市追高失败", {"subject_key": "rotation/B2", "facts": {"stance": "bearish"}, "lessons": ["降低追高仓位"]})
    second = memory.ingest("decision_review", "decision-2", "轮动市追高失败，确认风险", {"subject_key": "rotation/B2", "facts": {"stance": "bearish"}, "lessons": ["降低追高仓位", "等待确认"]})
    assert first[0]["action"] == "created"
    assert second[0]["action"] == "updated"
    record = MemoryRepository().get(first[0]["memory_id"])
    assert record is not None and "等待确认" in record.lessons

    decisions = DecisionService()
    decision = decisions.save_decision(query="测试", skill_slug="theme_momentum", market_regime="rotation_market", confidence=0.7)
    outcome = decisions.record_outcome(decision["decision_id"], date(2026, 8, 8), 5, benchmark_return=0.01, portfolio_return=-0.02)
    review = decisions.review(decision["decision_id"], {"decision_quality": 0.3, "lessons": ["高位退潮时主题动量必须降级"]}, outcome["outcome_id"])
    assert outcome["excess_return"] == -0.03
    assert review["memory_ids"]
