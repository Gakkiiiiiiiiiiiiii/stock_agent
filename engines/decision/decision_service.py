from __future__ import annotations

from datetime import date
from typing import Any

from engines.memory.service import MemoryService
from storage.bootstrap import create_all
from storage.repositories.research_repository import DecisionRepository


class DecisionService:
    """Persistence boundary for Decision → Outcome → Review → Memory."""

    def __init__(self, repository: DecisionRepository | None = None, memory_service: MemoryService | None = None) -> None:
        self.repository = repository or DecisionRepository()
        self.memory_service = memory_service or MemoryService()

    def save_decision(self, **payload: Any) -> dict:
        create_all()
        decision = self.repository.create(**payload)
        return {"decision_id": decision.id, "status": decision.status, "created_at": decision.created_at.isoformat()}

    def get_decision(self, decision_id: str) -> dict:
        create_all()
        decision = self.repository.get(decision_id)
        if decision is None:
            return {"found": False, "decision_id": decision_id}
        return {"found": True, "decision": self._dump(decision)}

    def record_outcome(self, decision_id: str, evaluation_date: date, horizon_days: int, **payload: Any) -> dict:
        create_all()
        if self.repository.get(decision_id) is None:
            return {"error": "DECISION_NOT_FOUND", "decision_id": decision_id}
        benchmark = payload.get("benchmark_return")
        portfolio = payload.get("portfolio_return")
        payload.setdefault("excess_return", portfolio - benchmark if portfolio is not None and benchmark is not None else None)
        outcome = self.repository.add_outcome(decision_id=decision_id, evaluation_date=evaluation_date, horizon_days=horizon_days, **payload)
        return {"outcome_id": outcome.id, "decision_id": decision_id, "excess_return": outcome.excess_return}

    def get_outcome(self, decision_id: str, horizon_days: int | None = None) -> dict:
        create_all()
        outcome = self.repository.get_outcome(decision_id, horizon_days)
        return {"found": outcome is not None, "outcome": self._dump(outcome) if outcome else None}

    def review(self, decision_id: str, review: dict[str, Any], outcome_id: int | None = None) -> dict:
        create_all()
        decision = self.repository.get(decision_id)
        if decision is None:
            return {"error": "DECISION_NOT_FOUND", "decision_id": decision_id}
        lessons = list(review.get("lessons") or [])
        memory_ids: list[int] = []
        if lessons:
            memory_result = self.memory_service.ingest(
                source_type="decision_review",
                source_id=decision_id,
                text="\n".join(lessons),
                metadata={
                    "memory_type": "STRATEGY_EXPERIENCE",
                    "subject_key": f"{decision.market_regime or 'UNKNOWN'}/{decision.skill_slug or 'research'}",
                    "facts": {"market_regime": decision.market_regime, "strategy": decision.skill_slug},
                    "lessons": lessons,
                    "decision_impact": 0.85,
                    "confidence": review.get("decision_quality", decision.confidence or 0.6),
                },
            )
            memory_ids = [item["memory_id"] for item in memory_result if item.get("memory_id")]
        review_row = self.repository.add_review(
            decision_id=decision_id,
            outcome_id=outcome_id,
            decision_quality=review.get("decision_quality"),
            what_was_correct=review.get("what_was_correct", []),
            what_was_wrong=review.get("what_was_wrong", []),
            root_causes=review.get("root_causes", []),
            unexpected_events=review.get("unexpected_events", []),
            lessons=lessons,
            memory_candidate_ids=memory_ids,
        )
        return {"review_id": review_row.id, "decision_id": decision_id, "memory_ids": memory_ids}

    @staticmethod
    def _dump(value: Any) -> dict:
        return {column.name: getattr(value, column.name) for column in value.__table__.columns}
