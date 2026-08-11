from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import inspect, text

from engines.decision.decision_service import DecisionService
from engines.memory.lifecycle import MemoryLifecycleService
from engines.memory.memory_retriever import retrieve_memory, scope_matches
from engines.memory.memory_scorer import MemoryScorer
from engines.memory.service import MemoryService
from storage.repositories.vector_repository import MemoryRepository


def _create_memory(status: str = "ACTIVE", **overrides) -> int:
    payload = {
        "memory_type": "STRATEGY_EXPERIENCE",
        "title": "轮动策略",
        "content": "轮动市场等待确认。",
        "source_type": "test",
        "status": status,
    }
    return MemoryRepository().create(**(payload | overrides)).id


def test_migration_021_applies_on_fresh_sqlite(isolated_database):
    inspector = inspect(isolated_database)
    assert "memory_evidence" in inspector.get_table_names()
    evidence_columns = {column["name"] for column in inspector.get_columns("memory_evidence")}
    assert {
        "id",
        "memory_id",
        "decision_id",
        "regime",
        "horizon_days",
        "market_excess_return",
        "sector_excess_return",
        "decision_quality",
        "applicability",
        "weight",
        "created_at",
    } <= evidence_columns
    memory_columns = {column["name"] for column in inspector.get_columns("memory_record")}
    assert {"applicable_market", "applicable_regimes", "applicable_styles", "applicable_horizon", "applicable_themes"} <= memory_columns
    with isolated_database.connect() as conn:
        versions = {row[0] for row in conn.execute(text("SELECT version FROM schema_migration")).fetchall()}
    assert "021_memory_lifecycle_v2.sql" in versions


def test_evidence_repository_round_trip_and_upsert(isolated_database):
    memory_id = _create_memory()
    repo = MemoryRepository()
    first = repo.add_evidence(memory_id, decision_id="d1", horizon_days=5, market_excess_return=0.02, decision_quality=0.8)
    assert first.id is not None
    # Same (memory_id, decision_id, horizon_days) key -> upsert refreshes the row.
    second = repo.add_evidence(memory_id, decision_id="d1", horizon_days=5, market_excess_return=-0.03, sector_excess_return=-0.01, decision_quality=0.8)
    assert second.id == first.id
    # A different horizon is a distinct event; anonymous events always append.
    repo.add_evidence(memory_id, decision_id="d1", horizon_days=20, market_excess_return=0.01)
    repo.add_evidence(memory_id, market_excess_return=0.01)
    repo.add_evidence(memory_id, market_excess_return=0.01)
    events = repo.list_evidence(memory_id)
    assert len(events) == 4
    assert events[0].market_excess_return == pytest.approx(-0.03)
    assert events[0].sector_excess_return == pytest.approx(-0.01)
    summary = repo.latest_evidence_summary(memory_id)
    assert summary["evidence_count"] == 4
    assert summary["avg_decision_quality"] == pytest.approx(0.8)
    assert summary["last_evidence_at"] is not None


def test_lifecycle_validates_after_enough_quality_support(isolated_database):
    memory_id = _create_memory()
    lifecycle = MemoryLifecycleService()
    state = {}
    for index in range(3):
        state = lifecycle.record_outcome_evidence(
            memory_id,
            decision_id=f"support-{index}",
            regime="rotation_market",
            horizon_days=5,
            market_excess_return=0.02,
            decision_quality=0.9,
        )
    assert state["status"] == "VALIDATED"
    assert state["evidence_count"] == 3
    assert state["weighted_confidence"] >= 0.7
    assert state["outcome_support_count"] == 3
    record = MemoryRepository().get(memory_id)
    assert record.metadata_json["weighted_confidence"] == pytest.approx(state["weighted_confidence"])
    assert record.metadata_json["evidence_count"] == 3
    assert len(MemoryRepository().list_evidence(memory_id)) == 3


def test_lifecycle_requires_revalidation_on_weighted_negative(isolated_database):
    memory_id = _create_memory(status="VALIDATED")
    lifecycle = MemoryLifecycleService()
    state = {}
    for index in range(3):
        state = lifecycle.record_outcome_evidence(
            memory_id,
            decision_id=f"failure-{index}",
            market_excess_return=-0.03,
            decision_quality=0.9,
        )
    assert state["status"] == "REVALIDATION_REQUIRED"
    assert state["weighted_confidence"] <= 0.3
    assert state["outcome_failure_count"] == 3


def test_lifecycle_does_not_flip_with_insufficient_evidence(isolated_database):
    memory_id = _create_memory()
    lifecycle = MemoryLifecycleService()
    state = lifecycle.record_outcome_evidence(memory_id, market_excess_return=0.05, decision_quality=1.0)
    assert state["status"] == "ACTIVE"  # 1 event < min_evidence_count
    state = lifecycle.record_outcome_evidence(memory_id, market_excess_return=0.05, decision_quality=1.0)
    assert state["status"] == "ACTIVE"  # 2 events < min_evidence_count


def test_lifecycle_noise_band_evidence_is_neutral(isolated_database):
    memory_id = _create_memory(status="VALIDATED")
    lifecycle = MemoryLifecycleService()
    state = {}
    for index in range(3):
        state = lifecycle.record_outcome_evidence(memory_id, decision_id=f"noise-{index}", market_excess_return=0.005)
    # Sub-threshold moves are neutral: they dilute but cannot trigger revalidation.
    assert state["status"] == "VALIDATED"
    assert 0.3 < state["weighted_confidence"] < 0.5


def test_lifecycle_legacy_positional_call_still_works(isolated_database):
    memory_id = _create_memory()
    lifecycle = MemoryLifecycleService()
    for _ in range(3):
        state = lifecycle.record_outcome_evidence(memory_id, 0.01)
    assert state["status"] == "VALIDATED"
    assert state["outcome_support_count"] == 3
    for _ in range(3):
        state = lifecycle.record_outcome_evidence(memory_id, -0.01)
    assert state["status"] == "REVALIDATION_REQUIRED"
    assert state["outcome_failure_count"] == 3


def test_ingest_persists_scope_from_facts_and_metadata(isolated_database):
    created = MemoryService().ingest(
        "decision_review",
        "scope-facts",
        "轮动市场等待确认",
        {"subject_key": "scope/facts", "facts": {"market_regime": "rotation_market", "applicable_regimes": ["rotation_market"]}},
    )
    record = MemoryRepository().get(created[0]["memory_id"])
    assert record.applicable_regimes == ["rotation_market"]
    assert record.applicable_market is None

    scoped = MemoryService().ingest(
        "decision_review",
        "scope-meta",
        "牛市趋势策略",
        {
            "subject_key": "scope/meta",
            "scope": {"regimes": ["trend_up"], "horizon_days": 5, "themes": ["AI"], "market": "A股"},
        },
    )
    record = MemoryRepository().get(scoped[0]["memory_id"])
    assert record.applicable_regimes == ["trend_up"]
    assert record.applicable_horizon == 5
    assert record.applicable_themes == ["AI"]
    assert record.applicable_market == "A股"

    unrestricted = MemoryService().ingest("manual_note", "scope-none", "通用经验", {"subject_key": "scope/none"})
    record = MemoryRepository().get(unrestricted[0]["memory_id"])
    assert record.applicable_regimes is None
    assert record.applicable_themes is None
    assert record.applicable_horizon is None


def test_merge_unions_scope_on_update(isolated_database):
    service = MemoryService()
    first = service.ingest("manual_note", "scope-merge-1", "轮动经验", {"subject_key": "scope/merge", "scope": {"regimes": ["rotation_market"]}})
    second = service.ingest("manual_note", "scope-merge-2", "轮动经验", {"subject_key": "scope/merge", "scope": {"regimes": ["bull"], "themes": ["AI"]}})
    assert second[0]["action"] == "updated"
    record = MemoryRepository().get(first[0]["memory_id"])
    assert sorted(record.applicable_regimes) == ["bull", "rotation_market"]
    assert record.applicable_themes == ["AI"]


def test_scope_matches_semantics():
    assert scope_matches({}, {"regime": "rotation_market"}) is True  # null scope matches all
    assert scope_matches({"applicable_regimes": ["rotation_market"]}, {"regime": "rotation_market"}) is True
    assert scope_matches({"applicable_regimes": ["trend_up"]}, {"regime": "rotation_market"}) is False
    assert scope_matches({"applicable_regimes": ["trend_up"]}, {}) is True  # missing context passes
    assert scope_matches({"applicable_themes": ["AI"]}, {"theme": "新能源"}) is False
    assert scope_matches({"applicable_horizon": 5}, {"horizon_days": 20}) is False
    assert scope_matches({"applicable_horizon": 5}, {"horizon_days": 5}) is True
    assert scope_matches({"applicable_market": "A股"}, {"market": "港股"}) is False


class _FakeRetriever:
    def __init__(self, contexts):
        self.contexts = contexts
        self.top_k = None

    def retrieve(self, query, **kwargs):
        self.top_k = kwargs.get("top_k")
        return {"contexts": list(self.contexts)}


def test_retriever_filters_non_matching_scope_before_ranking(isolated_database, monkeypatch):
    matching = _create_memory(applicable_regimes=["rotation_market"])
    other_regime = _create_memory(applicable_regimes=["trend_up"])
    unrestricted = _create_memory()
    contexts = [
        {"record": {"id": matching, "memory_type": "STRATEGY_EXPERIENCE", "status": "VALIDATED"}, "final_score": 0.9},
        {"record": {"id": other_regime, "memory_type": "STRATEGY_EXPERIENCE", "status": "VALIDATED"}, "final_score": 0.95},
        {"record": {"id": unrestricted, "memory_type": "STRATEGY_EXPERIENCE", "status": "VALIDATED"}, "final_score": 0.8},
    ]
    fake = _FakeRetriever(contexts)
    monkeypatch.setattr("engines.memory.memory_retriever.HybridRetriever", lambda: fake)
    seen_by_rank: list[list[int]] = []

    def capture_rank(self, items, regime):
        seen_by_rank.append([item["record"]["id"] for item in items])
        return items

    monkeypatch.setattr("engines.memory.memory_retriever.MemoryScorer.rank", capture_rank)
    result = retrieve_memory("轮动策略", market_regime="rotation_market", top_k=3)
    # The trend_up-only memory is dropped BEFORE ranking; null scope matches all.
    assert seen_by_rank[0] == [matching, unrestricted]
    assert [item["record"]["id"] for item in result["contexts"]] == [matching, unrestricted]
    # Scope filtering over-fetches so top_k stays filled after the hard filter.
    assert fake.top_k > 3


def test_retriever_without_scope_context_keeps_legacy_flow(isolated_database, monkeypatch):
    memory_id = _create_memory(applicable_regimes=["trend_up"])
    contexts = [{"record": {"id": memory_id, "memory_type": "STRATEGY_EXPERIENCE", "status": "VALIDATED"}, "final_score": 0.9}]
    fake = _FakeRetriever(contexts)
    monkeypatch.setattr("engines.memory.memory_retriever.HybridRetriever", lambda: fake)
    result = retrieve_memory("轮动策略", top_k=5)
    assert [item["record"]["id"] for item in result["contexts"]] == [memory_id]
    assert fake.top_k == 5


def test_scorer_adds_config_gated_weighted_confidence_bonus():
    context = {"record": {"metadata_json": {"weighted_confidence": 0.9}}, "final_score": 0.5}
    enabled = MemoryScorer(config={"scorer": {"confidence_bonus_enabled": True, "confidence_bonus_weight": 0.05}})
    disabled = MemoryScorer(config={"scorer": {"confidence_bonus_enabled": False, "confidence_bonus_weight": 0.05}})
    baseline = MemoryScorer(config={"scorer": {}})
    plain = baseline.score({"record": {"metadata_json": {}}, "final_score": 0.5})
    assert enabled.score(context) == pytest.approx(plain + 0.05 * 0.9)
    assert disabled.score(context) == pytest.approx(plain)


def test_review_records_structured_outcome_evidence(isolated_database, monkeypatch):
    monkeypatch.setattr("engines.decision.decision_service.advance_trading_days", lambda day, horizon: day + timedelta(days=horizon))
    service = DecisionService()
    decision = service.save_decision(
        skill_slug="theme_momentum",
        market_regime="rotation_market",
        thesis={"claim": "主题延续"},
        invalidation_conditions=["成交额缩量"],
        evidence_refs=["evidence://daily/1"],
    )
    outcome = service.record_outcome(
        decision["decision_id"],
        date(2026, 8, 14),
        5,
        benchmark_return=0.01,
        portfolio_return=0.03,
        market_excess_return=0.02,
        sector_excess_return=0.01,
    )
    saved = service.review(
        decision["decision_id"],
        {
            "lessons": ["等待确认"],
            "decision_quality": 0.9,
            "outcome_excess_return": 0.02,
            "attribution": {"correct": ["direction"], "wrong": [], "unknown": ["entry_timing"]},
        },
        outcome["outcome_id"],
    )
    memory_id = saved["memory_ids"][0]
    events = MemoryRepository().list_evidence(memory_id)
    assert len(events) == 1
    evidence = events[0]
    assert evidence.decision_id == decision["decision_id"]
    assert evidence.regime == "rotation_market"
    assert evidence.horizon_days == 5
    assert evidence.market_excess_return == pytest.approx(0.02)
    assert evidence.sector_excess_return == pytest.approx(0.01)
    assert evidence.decision_quality == pytest.approx(0.9)
    assert evidence.applicability == pytest.approx(0.5)  # 1 known of 2 attribution dimensions
    update = saved["memory_evidence_updates"][0]
    assert update["evidence_count"] == 1
    assert 0.0 <= update["weighted_confidence"] <= 1.0
