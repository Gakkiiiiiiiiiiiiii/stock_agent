from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from agent.executor import SkillExecutor
from app.skill_contract import FreshnessPolicy, SkillContractValidator, SkillExecutionContract, SkillExecutionState, SkillOutputContract
from app.skill_loader import SkillDefinition, load_skills
from app.tool_registry import ClaudeToolRegistry
from engines.decision.decision_service import DecisionService
from engines.decision.outcome_evaluator import DecisionOutcomeEvaluator
from engines.market.exchange_calendar import ExchangeTradingCalendar
from engines.memory.service import MemoryService
from engines.memory.lifecycle import MemoryLifecycleService
from engines.regime.regime_state_machine import PersistentRegimeStateMachine
from storage.bootstrap import create_all
from storage.db import SessionLocal, get_engine
from storage.repositories.vector_repository import MemoryRepository
from storage.repositories.job_repository import JobTaskRepository


def configure_test_database(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'learning_loop.db'}")
    get_engine.cache_clear()
    SessionLocal.configure(bind=get_engine())
    JobTaskRepository._schema_ready = False
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


def test_required_tool_error_and_stale_market_data_do_not_satisfy_contract():
    skill = SkillDefinition(
        slug="hardening", name="hardening", description="", content="",
        execution=SkillExecutionContract(required_tools=["get_market_regime", "get_market_snapshot"], require_fresh_market_data=True, freshness=FreshnessPolicy(require_same_trading_day=True, max_age_minutes=30)),
    )
    state = SkillExecutionState(skill_slug=skill.slug)
    state.record_tool_result("get_market_regime", {"error": {"code": "UNAVAILABLE"}})
    state.record_tool_result("get_market_snapshot", {"snapshot": {"as_of": "2026-08-07T08:00:00+00:00"}})
    violations = SkillContractValidator().validate(skill, state, "", now=date_to_datetime(2026, 8, 7, 10, 0))
    assert "REQUIRED_TOOL_FAILED:get_market_regime" in violations
    assert "MARKET_DATA_STALE" in violations


def test_freshness_uses_shanghai_market_open_and_session_dates(monkeypatch):
    # Friday close remains the active market session before Monday's opening.
    monkeypatch.setattr("app.skill_contract.ExchangeTradingCalendar.normalize", lambda _self, value: value.date())
    policy = FreshnessPolicy(require_same_trading_day=True, require_after_market_open=True)
    before_open = SkillContractValidator._freshness_violations(
        datetime(2026, 8, 10, 1, 29, tzinfo=UTC), policy, datetime(2026, 8, 10, 2, 0, tzinfo=UTC)
    )
    assert before_open == ["MARKET_DATA_BEFORE_MARKET_OPEN"]
    friday_data = SkillContractValidator._freshness_violations(
        datetime(2026, 8, 7, 8, 0, tzinfo=UTC), FreshnessPolicy(require_same_trading_day=True), datetime(2026, 8, 10, 2, 0, tzinfo=UTC)
    )
    assert friday_data == ["MARKET_DATA_NOT_SAME_TRADING_DAY"]


def test_outcome_evaluator_uses_next_session_open_and_configurable_benchmark(monkeypatch):
    monkeypatch.setattr("engines.decision.outcome_evaluator.advance_trading_days", lambda _day, _count: date(2026, 8, 10))

    def fake_kline(symbol, **_kwargs):
        prices = {
            "600000.SH": [{"date": "2026-08-10", "open": 10}, {"date": "2026-08-14", "close": 12}],
            "399001.SZ": [{"date": "2026-08-10", "open": 20}, {"date": "2026-08-14", "close": 21}],
        }
        return {"records": prices[symbol]}

    monkeypatch.setattr("engines.decision.outcome_evaluator.get_kline", fake_kline)
    result = DecisionOutcomeEvaluator().evaluate(
        {"decision_as_of": datetime(2026, 8, 7, 15, 0, tzinfo=UTC), "candidates": [{"symbol": "600000.SH"}], "benchmark_symbol": "399001.SZ"},
        date(2026, 8, 14),
    )
    assert result["portfolio_return"] == pytest.approx(0.2)
    assert result["benchmark_return"] == pytest.approx(0.05)
    assert result["realized_metrics"]["components"][0]["entry"]["price_type"] == "OPEN"
    assert result["realized_metrics"]["benchmark"]["symbol"] == "399001.SZ"


def test_knowledge_retrieval_does_not_satisfy_dedicated_memory_contract():
    skill = SkillDefinition(slug="memory", name="memory", description="", content="", execution=SkillExecutionContract(require_memory_lookup=True))
    state = SkillExecutionState(skill_slug=skill.slug)
    state.record_tool_result("retrieve_relevant_context", {"contexts": [{"source_type": "video_knowledge_unit"}]})
    assert "MEMORY_LOOKUP_REQUIRED: call a dedicated search_memory tool and obtain at least one memory result." in SkillContractValidator().validate(skill, state, "")
    state.record_tool_result("search_strategy_memory", {"contexts": [{"record": {"memory_type": "STRATEGY_EXPERIENCE"}}]})
    assert SkillContractValidator().validate(skill, state, "") == []


def test_every_skill_has_a_valid_contract():
    registry = ClaudeToolRegistry()
    known = set(registry._tools)
    for skill in load_skills():
        required = set(skill.execution.required_tools)
        optional = set(skill.execution.optional_tools)
        forbidden = set(skill.execution.forbidden_tools)
        assert required <= known
        assert optional <= known
        assert forbidden <= known
        assert not required & forbidden


def date_to_datetime(year, month, day, hour, minute):
    from datetime import UTC, datetime

    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_persistent_regime_uses_confirmation_days(monkeypatch, tmp_path):
    configure_test_database(monkeypatch, tmp_path)
    state_machine = PersistentRegimeStateMachine()
    state_machine.advance("CN_A", "rotation_market", date(2026, 8, 3))
    first = state_machine.advance("CN_A", "range_market", date(2026, 8, 4))
    second = state_machine.advance("CN_A", "range_market", date(2026, 8, 5))
    third = state_machine.advance("CN_A", "range_market", date(2026, 8, 6))
    assert first["switch_status"] == "watch_switch"
    assert second["candidate_days"] == 2
    assert third["confirmed_regime"] == "range_market"
    assert third["switch_status"] == "confirmed_switch"


def test_same_day_and_weekend_do_not_add_regime_confirmation_days(monkeypatch, tmp_path):
    configure_test_database(monkeypatch, tmp_path)
    state_machine = PersistentRegimeStateMachine()
    state_machine.advance("CN_A", "rotation_market", date(2026, 8, 7))
    weekend = state_machine.advance("CN_A", "range_market", date(2026, 8, 8))
    first = state_machine.advance("CN_A", "range_market", date(2026, 8, 10))
    same_day = state_machine.advance("CN_A", "range_market", date(2026, 8, 10))
    next_day = state_machine.advance("CN_A", "range_market", date(2026, 8, 11))
    assert weekend["candidate_days"] == 0
    assert first["candidate_days"] == 1
    assert same_day["candidate_days"] == 1
    assert next_day["candidate_days"] == 2


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


def test_review_job_waits_for_t5_outcome(monkeypatch, tmp_path):
    configure_test_database(monkeypatch, tmp_path)
    monkeypatch.setattr("engines.decision.decision_service.advance_trading_days", lambda day, horizon: day + timedelta(days=horizon))
    service = DecisionService(memory_service=object())
    decision = service.save_decision(query="测试", decision_as_of=datetime(2026, 8, 7, 15, 0, tzinfo=UTC))
    assert len(decision["evaluation_jobs"]) == 3
    t1 = service.record_outcome(decision["decision_id"], date(2026, 8, 8), 1, benchmark_return=0.01, portfolio_return=0.02)
    t5 = service.record_outcome(decision["decision_id"], date(2026, 8, 14), 5, benchmark_return=0.01, portfolio_return=0.02)
    assert t1["review_job_id"] is None
    assert t5["review_job_id"]
    review_job = JobTaskRepository().get(t5["review_job_id"])
    assert review_job and review_job["task_type"] == "decision_review"


def test_strategy_memory_lifecycle_tracks_consecutive_outcomes(monkeypatch, tmp_path):
    configure_test_database(monkeypatch, tmp_path)
    created = MemoryService().ingest(
        "decision_review", "lifecycle-decision", "轮动市场等待确认", {"subject_key": "rotation/lifecycle", "lessons": ["等待确认"]}
    )
    memory_id = created[0]["memory_id"]
    lifecycle = MemoryLifecycleService()
    for _ in range(3):
        state = lifecycle.record_outcome_evidence(memory_id, 0.01)
    assert state["status"] == "VALIDATED"
    assert state["outcome_support_count"] == 3
    for _ in range(3):
        state = lifecycle.record_outcome_evidence(memory_id, -0.01)
    assert state["status"] == "REVALIDATION_REQUIRED"
    assert state["outcome_failure_count"] == 3


def test_exchange_calendar_keeps_future_sessions_out_of_partial_qmt_cache(monkeypatch, tmp_path):
    configure_test_database(monkeypatch, tmp_path)
    monkeypatch.setattr(ExchangeTradingCalendar, "_remote_unavailable_until", None)

    class PartialBridge:
        def get_history(self, **_kwargs):
            return [{"date": "2026-08-07"}]

    calendar = ExchangeTradingCalendar(bridge=PartialBridge())
    assert calendar.normalize(date(2026, 8, 10)) == date(2026, 8, 10)


def test_exchange_calendar_skips_holiday_inside_qmt_coverage(monkeypatch, tmp_path):
    configure_test_database(monkeypatch, tmp_path)
    monkeypatch.setattr(ExchangeTradingCalendar, "_remote_unavailable_until", None)

    class HolidayBridge:
        def get_history(self, **_kwargs):
            return [{"date": "2026-09-30"}, {"date": "2026-10-09"}]

    calendar = ExchangeTradingCalendar(bridge=HolidayBridge())
    assert calendar.is_trading_day(date(2026, 10, 1)) is False
    assert calendar.advance_sessions(date(2026, 9, 30), 1) == date(2026, 10, 9)
