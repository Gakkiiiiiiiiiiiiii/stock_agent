from __future__ import annotations

from datetime import UTC, date, datetime
import json
from typing import Any

from engines.memory.service import MemoryService
from storage.bootstrap import create_all
from storage.repositories.research_repository import DecisionRepository
from storage.repositories.job_repository import JobTaskRepository
from engines.decision.evaluation_clock import outcome_not_before
from engines.market.market_clock import MarketClock
from engines.market.trading_calendar import advance_trading_days


class DecisionService:
    """Persistence boundary for Decision → Outcome → Review → Memory."""

    def __init__(self, repository: DecisionRepository | None = None, memory_service: MemoryService | None = None) -> None:
        self.repository = repository or DecisionRepository()
        self.memory_service = memory_service or MemoryService()

    def save_decision(self, **payload: Any) -> dict:
        create_all()
        payload.setdefault("decision_as_of", datetime.now(UTC))
        payload.setdefault("evaluation_anchor", "NEXT_SESSION_OPEN")
        payload.setdefault("benchmark_symbol", "000001.SH")
        decision = self.repository.create(**payload)
        jobs = self.schedule_evaluations(decision.id, MarketClock().calendar_date(decision.decision_as_of or decision.created_at))
        return {"decision_id": decision.id, "status": decision.status, "created_at": decision.created_at.isoformat(), "evaluation_jobs": jobs}

    @staticmethod
    def schedule_evaluations(decision_id: str, decision_date: date) -> list[str]:
        repo = JobTaskRepository()
        jobs = []
        for horizon in (1, 5, 20):
            due = advance_trading_days(decision_date, horizon)
            job = repo.create("decision_outcome", {"decision_id": decision_id, "horizon_days": horizon, "evaluation_date": due.isoformat()}, idempotency_key=f"decision-outcome:{decision_id}:{horizon}", not_before=outcome_not_before(due))
            jobs.append(job["id"])
        return jobs

    @staticmethod
    def enqueue_review(decision_id: str, outcome_id: int, horizon_days: int) -> str | None:
        if horizon_days != 5:
            return None
        task = JobTaskRepository().create(
            "decision_review", {"decision_id": decision_id, "horizon_days": horizon_days, "outcome_id": outcome_id},
            idempotency_key=f"decision-review:{decision_id}:{horizon_days}", not_before=datetime.now(UTC),
        )
        return task["id"]

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
        self.repository.update(decision_id, evaluation_status="OUTCOME_RECORDED", next_evaluation_date=None if horizon_days >= 20 else evaluation_date)
        review_job_id = self.enqueue_review(decision_id, outcome.id, horizon_days)
        return {"outcome_id": outcome.id, "decision_id": decision_id, "excess_return": outcome.excess_return, "review_job_id": review_job_id}

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
        outcome = self.repository.get_outcome_by_id(outcome_id) if outcome_id is not None else None
        memory_ids: list[int] = []
        if lessons:
            memory_result = self.memory_service.ingest(
                source_type="decision_review",
                source_id=decision_id,
                text=json.dumps(
                    {
                        "decision": {
                            "skill_slug": decision.skill_slug,
                            "market_regime": decision.market_regime,
                            "thesis": decision.thesis,
                            "trigger_conditions": decision.trigger_conditions,
                            "invalidation_conditions": decision.invalidation_conditions,
                        },
                        "outcome": self._json_safe(self._dump(outcome)) if outcome else {},
                        "review": review,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
                metadata={
                    "memory_type": "STRATEGY_EXPERIENCE",
                    "subject_key": f"{decision.market_regime or 'UNKNOWN'}/{decision.skill_slug or 'research'}",
                    "facts": {
                        "market_regime": decision.market_regime,
                        "strategy": decision.skill_slug,
                        "applicable_regimes": list(review.get("applicable_regimes") or review.get("applicable_regime") or []),
                        "root_causes": list(review.get("root_causes") or []),
                        "invalidation_conditions": list(review.get("invalidation_updates") or []),
                        "outcome": self._json_safe(self._dump(outcome)) if outcome else {},
                    },
                    "lessons": lessons,
                    "decision_impact": 0.85,
                    "confidence": review.get("decision_quality", decision.confidence or 0.6),
                },
            )
            memory_ids = [item["memory_id"] for item in memory_result if item.get("memory_id")]
        evidence_updates: list[dict] = []
        excess_return = review.get("outcome_excess_return")
        if memory_ids and isinstance(excess_return, (int, float)):
            from engines.memory.lifecycle import MemoryLifecycleService

            lifecycle = MemoryLifecycleService()
            evidence_updates = [lifecycle.record_outcome_evidence(memory_id, float(excess_return)) for memory_id in memory_ids]
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
            applicable_regimes=list(review.get("applicable_regimes") or review.get("applicable_regime") or []),
            invalidation_updates=list(review.get("invalidation_updates") or []),
            regime_path=list(review.get("regime_path") or []),
            evidence_refs=list(review.get("evidence_refs") or []),
            review_mode=review.get("review_mode") or "structured",
            review_model=review.get("review_model"),
        )
        self.repository.update(decision_id, evaluation_status="REVIEWED", reviewed_at=datetime.now(UTC))
        return {"review_id": review_row.id, "decision_id": decision_id, "memory_ids": memory_ids, "memory_evidence_updates": evidence_updates}

    def annotate_review(self, review_id: int, **payload: Any) -> dict:
        review = self.repository.update_review(review_id, **payload)
        return self._dump(review)

    @staticmethod
    def _dump(value: Any) -> dict:
        return {column.name: getattr(value, column.name) for column in value.__table__.columns}

    @staticmethod
    def _json_safe(value: Any) -> Any:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
