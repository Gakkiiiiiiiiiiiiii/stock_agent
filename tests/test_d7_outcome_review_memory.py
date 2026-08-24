from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from contracts.decision_memory import DecisionMemory, DecisionMemoryCandidate
from contracts.outcome import DecisionOutcome
from contracts.review import DecisionReview, ReviewPoint
from contracts.decision import PolicyEvaluation
from contracts.proposal import DecisionHorizon, InvestmentProposalV2, ModelIdentity
from agent.contracts import SpecialistArtifact, SpecialistRole, ToolUsage
from storage.repositories.research_repository import DecisionRepository, OutcomeRepository, ReviewRepository, DecisionMemoryRepository
from storage.models.research import DecisionBundleBindingRecord
from storage.db import session_scope
from storage.repositories.decision_input_repository import DecisionInputBundleRepository
from contracts.decision_input import build_bundle
from storage.repositories.research_repository import DecisionSnapshotRepository
from engines.decision.outcome_service import OutcomeService
from engines.decision.review_service import ReviewService
from engines.market.trading_clock import QuantTradingCalendarAdapter, TradingClock, WeekdayTradingCalendar, configure_default_clock, get_default_clock


NOW = datetime(2026, 8, 24, 16, tzinfo=UTC)


def _decision() -> str:
    return DecisionRepository().create(query="d7", candidates=[], decision_as_of=NOW).id


def test_outcome_quant_only_t1_and_no_lookahead():
    decision_id = "d1"
    with pytest.raises(ValueError):
        DecisionOutcome.build(decision_id=decision_id, horizon="T+1", measured_at=NOW, available_at=NOW, source_system="content", source_snapshot_refs=["s1"])
    with pytest.raises(ValueError):
        DecisionOutcome.build(decision_id=decision_id, horizon="T+1", measured_at=NOW, decision_as_of=NOW + timedelta(minutes=1), source_snapshot_refs=["s1"])
    item = DecisionOutcome.build(decision_id=decision_id, horizon="T+1", measured_at=NOW, available_at=NOW, source_snapshot_refs=["q:s1"])
    assert item.source_system == "quant"
    friday = datetime(2026, 8, 21, 16, tzinfo=UTC)
    monday = datetime(2026, 8, 24, 16, tzinfo=UTC)
    next_session = DecisionOutcome.build(decision_id=decision_id, horizon="T+1", decision_as_of=friday, measured_at=monday, available_at=monday, source_snapshot_refs=["q:next-session"])
    assert next_session.measured_at.date() == monday.date()


def test_outcome_review_memory_storage_isolated_and_idempotent(isolated_database):
    decision_id = _decision()
    outcome = DecisionOutcome.build(decision_id=decision_id, horizon="T+1", measured_at=NOW, available_at=NOW, source_snapshot_refs=["q:s1"])
    saved = OutcomeRepository().save(outcome)
    assert OutcomeRepository().save(outcome).outcome_id == saved.outcome_id
    review = DecisionReview.build(
        review_id="review-1", decision_id=decision_id, outcome_refs=[outcome.outcome_id], created_at=NOW,
        correct_judgments=[ReviewPoint(category="evidence", statement="correct", evidence_refs=["q:s1"])],
    )
    assert ReviewRepository().save(review).review_id == "review-1"
    candidate = DecisionMemoryCandidate(memory_type="SUCCESS_PATTERN", content="T+1 signal calibrated", source_decision_ids=[decision_id], confidence=.7)
    memory = DecisionMemory.build(memory_type=candidate.memory_type, content=candidate.content, source_decision_ids=candidate.source_decision_ids, scope=candidate.scope, confidence=candidate.confidence, created_at=NOW, provenance={"review_id": review.review_id})
    assert DecisionMemoryRepository().save(memory).memory_id == memory.memory_id
    assert DecisionMemoryRepository().save(memory).memory_id == memory.memory_id
    with pytest.raises(ValueError):
        DecisionMemoryRepository().save(memory.model_copy(update={"content": "different"}))


def test_review_requires_outcome_and_memory_self_loop_rejected(isolated_database):
    decision_id = _decision()
    with pytest.raises(ValueError):
        ReviewRepository().save(DecisionReview.build(review_id="r", decision_id=decision_id, outcome_refs=["missing"], created_at=NOW))
    with pytest.raises(ValueError):
        DecisionMemory.build(memory_type="FAILURE_PATTERN", content="self", confidence=.5, created_at=NOW, provenance={"source": "decision_memory"})
    with pytest.raises(ValueError):
        DecisionMemory.build(memory_type="FAILURE_PATTERN", content="nested self", confidence=.5, created_at=NOW, provenance={"trace": [{"memory_ref": "decision-memory:other"}]})


def test_default_outcome_service_uses_injected_quant_and_stores_attribution(isolated_database):
    decision_id = DecisionRepository().create(id="d-service", query="service", candidates=[{"symbol": "AAA"}], decision_as_of=datetime(2026, 8, 21, 10, tzinfo=UTC), benchmark_symbol="IDX").id

    calls = []
    class Quant:
        def get_bars(self, symbols, start, end, *, adjust="qfq"):
            calls.append((tuple(symbols), start, end))
            return {
                "contract_version": "market-data.v1", "service_version": "q1", "available_at": "2026-08-24T16:00:00Z",
                "AAA": [{"date": "2026-08-24", "open": 10, "close": 11, "available_at": "2026-08-24T16:00:00Z"}],
                "IDX": [{"date": "2026-08-24", "open": 100, "close": 105, "available_at": "2026-08-24T16:00:00Z"}],
            }

    outcome = OutcomeService(quant=Quant(), clock=lambda: NOW, calendar=WeekdayTradingCalendar()).refresh(decision_id=decision_id, horizon="T+1", measured_at=NOW)
    assert outcome.excess_return_pct == pytest.approx(.05)
    assert outcome.attribution["source_system"] == "quant"
    assert outcome.attribution["service_version"] == "q1"
    retry = OutcomeService(quant=Quant(), clock=lambda: NOW, calendar=WeekdayTradingCalendar()).refresh(decision_id=decision_id, horizon="T+1", measured_at=NOW)
    assert retry.outcome_id == outcome.outcome_id
    assert len(calls) == 1


def test_review_service_retries_stable_review_and_memory_ids():
    outcome = DecisionOutcome.build(decision_id="review-d", horizon="T+1", measured_at=NOW, available_at=NOW, excess_return_pct=.1, source_snapshot_refs=["q:s1"])
    class Snapshot:
        snapshot_id = "snap-review"
        schema_version = "decision.snapshot.v3"
        policy = {"approved": True}
        proposal = {"payload": {"evidence_refs": ["q:s1"]}}
    class Outcomes:
        def get(self, ref): return outcome if ref == outcome.outcome_id else None
    class Reviews:
        def __init__(self): self.items = {}
        def get(self, ref): return self.items.get(ref)
        def save(self, review):
            class Row: pass
            row = Row(); row.payload_json = review.model_dump(mode="json")
            self.items[review.review_id] = review
            return row
    class Memories:
        def __init__(self): self.items = {}
        def build_from_candidate(self, candidate, *, decision_id, review_id, created_at):
            memory = DecisionMemoryRepository.build_from_candidate(candidate, decision_id=decision_id, review_id=review_id, created_at=created_at)
            return memory
        def save(self, memory):
            self.items[memory.memory_id] = memory
    outcomes, reviews, memories = Outcomes(), Reviews(), Memories()
    service = ReviewService(outcomes=outcomes, reviews=reviews, memories=memories, snapshots=type("Snapshots", (), {"get_v3_for_decision": lambda self, _: Snapshot()})(), clock=lambda: NOW)
    first = service.save_review(decision_id="review-d", outcome_refs=[outcome.outcome_id])
    second = service.save_review(decision_id="review-d", outcome_refs=[outcome.outcome_id])
    assert second.review_id == first.review_id
    assert list(memories.items) == [next(iter(memories.items))]
    assert len(reviews.items) == 1


def test_formal_segments_reject_cross_decision_lineage_and_allow_same_anchor_idempotency(isolated_database):
    first = DecisionRepository().create(id="segment-d1", query="d1", candidates=[], decision_as_of=NOW)
    second = DecisionRepository().create(id="segment-d2", query="d2", candidates=[], decision_as_of=NOW)
    bundle_one = build_bundle(created_at=NOW, decision_time=NOW, task_type="test", objective="d1")
    bundle_two = build_bundle(created_at=NOW, decision_time=NOW, task_type="test", objective="d2")
    bundle_repo = DecisionInputBundleRepository()
    bundle_repo.save(bundle_one)
    bundle_repo.save(bundle_two)
    artifact = SpecialistArtifact(task_id="task-1", specialist=SpecialistRole.MARKET, tool_usage=ToolUsage())
    proposal = InvestmentProposalV2.build(
        proposal_id="shared-proposal", subject_type="PORTFOLIO", subject_key=None, action="HOLD",
        target_weight=None, weight_delta=None, confidence=.5, horizon=DecisionHorizon(period="decision"),
        thesis=[], catalysts=[], entry_conditions=[], invalidation_conditions=[], expected_risks=[],
        evidence_refs=[], specialist_artifact_refs=[], unknowns=[], generated_by=ModelIdentity(provider="test", model="test"),
    )
    policy = PolicyEvaluation.build(
        policy_result_id="shared-policy", policy_version="policy.v1", approved=True,
        original_value=None, adjusted_value=None,
        checks=[{"rule_id": "r1", "rule_version": "policy.v1", "passed": True, "severity": "INFO", "input_snapshot": {}, "original_value": None, "adjusted_value": None, "reason_code": "OK", "reason": "ok"}],
    )
    repository = DecisionSnapshotRepository()
    repository.save_formal_segments(decision_id=first.id, bundle_id=bundle_one.bundle_id, artifacts=[artifact], proposal=proposal, policy=policy)
    repository.save_formal_segments(decision_id=first.id, bundle_id=bundle_one.bundle_id, artifacts=[artifact], proposal=proposal, policy=policy)
    with session_scope() as session:
        binding = session.get(DecisionBundleBindingRecord, first.id)
        assert binding is not None and binding.bundle_id == bundle_one.bundle_id
    with pytest.raises(ValueError, match="binding conflict"):
        repository.save_formal_segments(decision_id=second.id, bundle_id=bundle_one.bundle_id, artifacts=[], proposal=proposal, policy=policy)
    with pytest.raises(ValueError, match="artifact lineage"):
        repository.save_formal_segments(decision_id=second.id, bundle_id=bundle_two.bundle_id, artifacts=[artifact], proposal=proposal, policy=policy)
    with pytest.raises(ValueError, match="proposal lineage"):
        repository.save_formal_segments(decision_id=second.id, bundle_id=bundle_two.bundle_id, artifacts=[], proposal=proposal, policy=policy)
    proposal_values = proposal.model_dump(mode="python", exclude={"proposal_hash"})
    proposal_values["proposal_id"] = "other-proposal"
    proposal_two = InvestmentProposalV2.build(**proposal_values)
    with pytest.raises(ValueError, match="policy lineage"):
        repository.save_formal_segments(decision_id=second.id, bundle_id=bundle_two.bundle_id, artifacts=[], proposal=proposal_two, policy=policy)


def test_default_outcome_service_uses_quant_calendar_even_when_process_clock_is_degraded(isolated_database):
    previous_clock = get_default_clock()
    configure_default_clock(TradingClock(calendar=WeekdayTradingCalendar()))
    try:
        decision_id = DecisionRepository().create(
            id="calendar-service", query="calendar", candidates=[{"symbol": "AAA"}],
            decision_as_of=datetime(2026, 8, 21, 10, tzinfo=UTC), benchmark_symbol="IDX",
        ).id

        class Quant:
            def get_trading_calendar(self, start, end, *, market_code="CN_A"):
                return {"sessions": [
                    {"date": "2026-08-21", "is_open": True},
                    {"date": "2026-08-24", "is_open": False},
                    {"date": "2026-08-25", "is_open": True},
                ]}

            def get_bars(self, symbols, start, end, *, adjust="qfq"):
                return {
                    "available_at": "2026-08-25T16:00:00Z",
                    "AAA": [{"date": "2026-08-25", "open": 10, "close": 11, "available_at": "2026-08-25T16:00:00Z"}],
                    "IDX": [{"date": "2026-08-25", "open": 100, "close": 105, "available_at": "2026-08-25T16:00:00Z"}],
                }

        service = OutcomeService(quant=Quant())
        assert isinstance(service.calendar, QuantTradingCalendarAdapter)
        outcome = service.refresh(decision_id=decision_id, horizon="T+1", measured_at=datetime(2026, 8, 25, 16, tzinfo=UTC))
        assert outcome.measured_at.date().isoformat() == "2026-08-25"
    finally:
        configure_default_clock(previous_clock)


def test_weekday_calendar_requires_explicit_offline_switch():
    service = OutcomeService(quant=object(), offline_calendar=True)
    assert isinstance(service.calendar, WeekdayTradingCalendar)
