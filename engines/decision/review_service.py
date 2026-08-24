"""Deterministic D7 review over persisted v3 snapshot and outcomes."""
from __future__ import annotations

from datetime import UTC, datetime

from contracts.decision_memory import DecisionMemoryCandidate
from contracts.review import DecisionReview, ReviewPoint
from contracts.proposal import _hash
from storage.repositories.research_repository import DecisionMemoryRepository, DecisionSnapshotRepository, OutcomeRepository, ReviewRepository


class ReviewService:
    def __init__(self, *, outcomes: OutcomeRepository | None = None, reviews: ReviewRepository | None = None, memories: DecisionMemoryRepository | None = None, snapshots: DecisionSnapshotRepository | None = None, clock=None) -> None:
        self.outcomes = outcomes or OutcomeRepository()
        self.reviews = reviews or ReviewRepository()
        self.memories = memories or DecisionMemoryRepository()
        self.snapshots = snapshots or DecisionSnapshotRepository()
        self.clock = clock

    def _now(self) -> datetime:
        value = self.clock() if callable(self.clock) else self.clock.now() if self.clock is not None and hasattr(self.clock, "now") else datetime.now(UTC)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("review clock must return timezone-aware datetime")
        return value

    @staticmethod
    def _review_id(decision_id: str, outcome_refs: list[str]) -> str:
        normalized = sorted({str(ref) for ref in outcome_refs})
        return f"review-{_hash({'decision_id': decision_id, 'outcome_refs': normalized})[:32]}"

    def build_review(self, *, decision_id: str, outcome_refs: list[str]) -> dict:
        snapshot = self.snapshots.get_v3_for_decision(decision_id)
        if snapshot is None:
            raise ValueError("DECISION_SNAPSHOT_NOT_FOUND")
        outcomes = [self.outcomes.get(ref) for ref in outcome_refs]
        if any(item is None for item in outcomes):
            raise ValueError("OUTCOME_NOT_FOUND")
        if any(item.decision_id != decision_id for item in outcomes if item is not None):
            raise ValueError("OUTCOME_DECISION_MISMATCH")
        values = [float(item.excess_return_pct or 0.0) for item in outcomes if item is not None]
        excess = sum(values) / len(values) if values else 0.0
        point = ReviewPoint(category="policy_effectiveness", statement="policy approved the observed proposal" if snapshot.policy.get("approved") else "policy vetoed the observed proposal", evidence_refs=list(snapshot.proposal.get("payload", {}).get("evidence_refs") or []))
        correct = [ReviewPoint(category="return", statement="decision produced positive excess return", evidence_refs=list(outcome_refs))] if excess > 0 else []
        incorrect = [ReviewPoint(category="return", statement="decision did not produce positive excess return", evidence_refs=list(outcome_refs))] if excess <= 0 else []
        candidates = [DecisionMemoryCandidate(memory_type="SUCCESS_PATTERN" if excess > 0 else "FAILURE_PATTERN", content="Observed positive excess return for the frozen decision" if excess > 0 else "Observed non-positive excess return for the frozen decision", source_decision_ids=[decision_id], scope={"schema_version": snapshot.schema_version}, confidence=min(1.0, 0.5 + abs(excess)),)]
        return DecisionReview.build(review_id=self._review_id(decision_id, outcome_refs), decision_id=decision_id, outcome_refs=outcome_refs, correct_judgments=correct, incorrect_judgments=incorrect, missed_evidence=[], overweighted_evidence=[], underweighted_evidence=[], policy_effectiveness=[point], confidence_calibration_error=None, proposed_memory_items=candidates, created_at=self._now()).model_dump(mode="python")

    def save_review(self, *, decision_id: str, outcome_refs: list[str]) -> DecisionReview:
        stable_review_id = self._review_id(decision_id, outcome_refs)
        existing = self.reviews.get(stable_review_id)
        if existing is not None:
            self._materialize_memories(existing, decision_id=decision_id)
            return existing
        review = DecisionReview.model_validate(self.build_review(decision_id=decision_id, outcome_refs=outcome_refs))
        # Persist the review anchor before materializing memory candidates.  A
        # failed review write must never leave a memory whose provenance points
        # at a review that does not exist.
        row = self.reviews.save(review)
        persisted = DecisionReview.model_validate(row.payload_json)
        self._materialize_memories(persisted, decision_id=decision_id)
        return persisted

    def _materialize_memories(self, review: DecisionReview, *, decision_id: str) -> None:
        """Materialize candidates after the review anchor, idempotently.

        A review may commit successfully while a downstream memory write
        fails.  Retrying the same natural review request must therefore
        resume the missing memory writes instead of returning early.
        """
        for candidate in review.proposed_memory_items:
            memory = self.memories.build_from_candidate(candidate, decision_id=decision_id, review_id=review.review_id, created_at=review.created_at)
            self.memories.save(memory)
