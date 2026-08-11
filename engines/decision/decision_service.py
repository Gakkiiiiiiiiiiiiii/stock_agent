from __future__ import annotations

from datetime import UTC, date, datetime
import json
from typing import Any

from engines.decision.benchmark_router import BenchmarkRouter
from engines.memory.service import MemoryService
from financial_agent.config import load_yaml_config
from storage.bootstrap import create_all
from storage.repositories.research_repository import DecisionRepository
from storage.repositories.job_repository import JobTaskRepository
from engines.decision.evaluation_clock import outcome_not_before
from engines.market.market_clock import MarketClock
from engines.market.trading_calendar import advance_trading_days

# 仅用于基准路由、不落库为独立列的决策属性（路由结果持久化在 benchmark_route 中）。
_ROUTING_ONLY_KEYS = ("decision_type", "style", "sector", "market")


def _evaluation_config() -> dict:
    try:
        return dict(load_yaml_config("decision_evaluation.yaml").get("decision_evaluation") or {})
    except FileNotFoundError:
        return {}


def evaluation_horizons() -> list[int]:
    """评估horizon序列（默认 [1, 5, 20]），由 config/decision_evaluation.yaml 配置。"""
    horizons = [int(item["days"]) for item in _evaluation_config().get("horizons") or [] if isinstance(item, dict) and item.get("days")]
    return horizons or [1, 5, 20]


def primary_horizon(decision_type: str | None = None) -> int:
    """触发复盘的主 horizon（默认 5），支持按 decision_type 覆盖。"""
    primary = _evaluation_config().get("primary_horizon") or {}
    if decision_type and isinstance(primary.get("by_decision_type"), dict):
        value = primary["by_decision_type"].get(decision_type)
        if value:
            return int(value)
    return int(primary.get("default") or 5)


class DecisionService:
    """Persistence boundary for Decision → Outcome → Review → Memory."""

    def __init__(self, repository: DecisionRepository | None = None, memory_service: MemoryService | None = None) -> None:
        self.repository = repository or DecisionRepository()
        self.memory_service = memory_service or MemoryService()

    def save_decision(self, **payload: Any) -> dict:
        create_all()
        route_attrs = {key: payload.pop(key) for key in _ROUTING_ONLY_KEYS if key in payload}
        route_attrs.setdefault("symbols", [str(item["symbol"]) for item in payload.get("candidates") or [] if isinstance(item, dict) and item.get("symbol")])
        route_attrs.setdefault("themes", list(payload.get("themes") or []))
        route = BenchmarkRouter().route(route_attrs)
        payload.setdefault("decision_as_of", datetime.now(UTC))
        payload.setdefault("evaluation_anchor", "NEXT_SESSION_OPEN")
        if payload.get("benchmark_symbol"):
            route["primary_benchmark"] = str(payload["benchmark_symbol"])
            route["reason"] = f"{route['reason']} | 调用方显式指定 benchmark_symbol"
        else:
            payload["benchmark_symbol"] = route["primary_benchmark"]
        payload.setdefault("benchmark_route", route)
        decision = self.repository.create(**payload)
        jobs = self.schedule_evaluations(decision.id, MarketClock().calendar_date(decision.decision_as_of or decision.created_at))
        return {"decision_id": decision.id, "status": decision.status, "created_at": decision.created_at.isoformat(), "evaluation_jobs": jobs}

    @staticmethod
    def schedule_evaluations(decision_id: str, decision_date: date) -> list[str]:
        repo = JobTaskRepository()
        jobs = []
        for horizon in evaluation_horizons():
            due = advance_trading_days(decision_date, horizon)
            job = repo.create("decision_outcome", {"decision_id": decision_id, "horizon_days": horizon, "evaluation_date": due.isoformat()}, idempotency_key=f"decision-outcome:{decision_id}:{horizon}", not_before=outcome_not_before(due))
            jobs.append(job["id"])
        return jobs

    @staticmethod
    def enqueue_review(decision_id: str, outcome_id: int, horizon_days: int) -> str | None:
        if horizon_days != primary_horizon():
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
        benchmark = payload.get("benchmark_return", payload.get("market_return"))
        portfolio = payload.get("portfolio_return", payload.get("absolute_return"))
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
                        "attribution": review.get("attribution") or {},
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
                        "attribution": review.get("attribution") or {},
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
            attribution = review.get("attribution") or {}
            evidence_kwargs = {
                "decision_id": decision_id,
                "regime": decision.market_regime,
                "horizon_days": outcome.horizon_days if outcome is not None else None,
                "market_excess_return": float(outcome.market_excess_return) if outcome is not None and outcome.market_excess_return is not None else float(excess_return),
                "sector_excess_return": float(outcome.sector_excess_return) if outcome is not None and outcome.sector_excess_return is not None else None,
                "decision_quality": review.get("decision_quality") if isinstance(review.get("decision_quality"), (int, float)) else None,
                "applicability": self._evidence_applicability(attribution),
            }
            evidence_updates = [lifecycle.record_outcome_evidence(memory_id, float(excess_return), **evidence_kwargs) for memory_id in memory_ids]
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
            attribution_json=review.get("attribution"),
        )
        self.repository.update(decision_id, evaluation_status="REVIEWED", reviewed_at=datetime.now(UTC))
        return {"review_id": review_row.id, "decision_id": decision_id, "memory_ids": memory_ids, "memory_evidence_updates": evidence_updates}

    def annotate_review(self, review_id: int, **payload: Any) -> dict:
        review = self.repository.update_review(review_id, **payload)
        return self._dump(review)

    @staticmethod
    def _evidence_applicability(attribution: dict[str, Any]) -> float | None:
        """Derive evidence applicability from the review attribution.

        Explicit ``attribution["applicability"]`` wins when present; otherwise the
        share of attributable (non-unknown) dimensions is used — an outcome whose
        dimensions are mostly unknown is weak evidence for the strategy memory.
        """
        if not isinstance(attribution, dict) or not attribution:
            return None
        explicit = attribution.get("applicability")
        if isinstance(explicit, (int, float)):
            return float(explicit)
        correct = list(attribution.get("correct") or [])
        wrong = list(attribution.get("wrong") or [])
        unknown = list(attribution.get("unknown") or [])
        total = len(correct) + len(wrong) + len(unknown)
        if not total:
            return None
        return (len(correct) + len(wrong)) / total

    @staticmethod
    def _dump(value: Any) -> dict:
        return {column.name: getattr(value, column.name) for column in value.__table__.columns}

    @staticmethod
    def _json_safe(value: Any) -> Any:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
