from __future__ import annotations

from datetime import UTC, date, datetime
import json
from typing import Any

from engines.decision.benchmark_router import BenchmarkRouter
from engines.decision.runtime_mode import RuntimeMode, build_runtime_segment
from engines.memory.service import MemoryService
from financial_agent.config import load_yaml_config
from storage.bootstrap import create_all
from storage.repositories.research_repository import DecisionRepository, DecisionSnapshotRepository
from storage.repositories.job_repository import JobTaskRepository
from engines.decision.evaluation_clock import outcome_not_before
from engines.market.market_clock import MarketClock
from engines.market.trading_calendar import advance_trading_days
from engines.versioning import get_version

# 仅用于基准路由、不落库为独立列的决策属性（路由结果持久化在 benchmark_route 中）。
_ROUTING_ONLY_KEYS = ("decision_type", "style", "sector", "market")

# 详细修改方案 §5：DecisionSnapshot v2 固定 Schema 版本。
DECISION_SNAPSHOT_SCHEMA_V2 = "decision.snapshot.v2"


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
        self.snapshots = DecisionSnapshotRepository()

    def save_decision(self, **payload: Any) -> dict:
        create_all()
        snapshot_input = payload.pop("decision_snapshot", None) or {}
        decision_quality = payload.pop("decision_quality", None)
        route_attrs = {key: payload.pop(key) for key in _ROUTING_ONLY_KEYS if key in payload}
        # Persist every router input so replay can reconstruct the historical
        # benchmark decision rather than silently using today's defaults.
        payload.update(route_attrs)
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
        payload.setdefault("benchmark_route_input", route_attrs)
        payload.setdefault("market_feature_version", get_version("market_feature_version"))
        payload.setdefault("opportunity_ranking_version", get_version("opportunity_ranking_version"))
        payload.setdefault("portfolio_rule_version", get_version("portfolio_rule_version"))
        payload.setdefault("benchmark_router_version", route.get("router_version") or get_version("benchmark_router_version"))
        payload.setdefault("supervisor_version", "v1" if payload.get("agent_run_id") else None)
        decision = self.repository.create(**payload)
        snapshot = self._save_decision_snapshot(decision, snapshot_input, decision_quality)
        jobs = self.schedule_evaluations(decision.id, MarketClock().calendar_date(decision.decision_as_of or decision.created_at))
        return {"decision_id": decision.id, "status": decision.status, "created_at": decision.created_at.isoformat(), "evaluation_jobs": jobs, "decision_snapshot_id": snapshot.snapshot_id}

    def _save_decision_snapshot(self, decision: Any, provided: dict[str, Any], decision_quality: str | None) -> Any:
        """落库 DecisionSnapshot（收尾文档 §38/§39）：固定 Schema + 可重放版本锚点与 lineage。"""
        market = dict(provided.get("market") or {})
        market.setdefault("snapshot_id", (decision.market_features or {}).get("data_snapshot_id"))
        market.setdefault("data_version", (decision.market_features or {}).get("data_version"))
        market.setdefault("source", (decision.market_features or {}).get("source"))
        as_of = market.get("as_of") or decision.data_as_of or decision.decision_as_of or decision.created_at
        market["as_of"] = as_of.isoformat() if isinstance(as_of, datetime) else as_of
        content = dict(provided.get("content") or {})
        content.setdefault("signal_contract", "content-factor-signal.v2")
        factor = dict(provided.get("factor") or {})
        factor.setdefault("alpha_score_contract", "factor.v1")
        strategy = dict(provided.get("strategy") or {
            "strategy_version": f"{decision.skill_slug or 'skill'}@v{decision.skill_version}" if decision.skill_version else decision.skill_slug,
            "skill_contract_hash": decision.skill_contract_hash,
            "skill_markdown_hash": decision.skill_markdown_hash,
        })
        strategy.setdefault("strategy_id", decision.skill_slug)
        agent = dict(provided.get("agent") or {
            "agent_version": decision.supervisor_version,
            "participating_agents": decision.participating_agents,
        })
        agent.setdefault("agent_run_id", decision.agent_run_id)
        agent.setdefault("supervisor_version", decision.supervisor_version)
        model = dict(provided.get("model") or {})
        # 兼容旧输入：agent 段遗留的模型字段迁移到独立 model 段（§38）
        model.setdefault("provider", agent.get("provider"))
        model.setdefault("model", agent.get("model_name") or agent.get("model"))
        model.setdefault("model_version", agent.get("model_version"))
        model.setdefault("prompt_version", agent.get("prompt_version"))
        portfolio = dict(provided.get("portfolio") or {})
        portfolio.setdefault("portfolio_policy_version", decision.portfolio_rule_version)
        portfolio.setdefault("portfolio_rule_version", decision.portfolio_rule_version)
        risk = dict(provided.get("risk") or {"risk_policy_version": provided.get("risk_policy_version")})
        risk.setdefault("risk_rule_version", risk.get("risk_policy_version") or provided.get("risk_rule_version"))
        # 详细修改方案 §4：runtime 段必须显式（fallback 不得隐式）。
        runtime = dict(provided.get("runtime") or {})
        if not runtime.get("runtime_mode"):
            mode = RuntimeMode.PRIMARY_AGENT if decision.agent_run_id else RuntimeMode.DETERMINISTIC_FALLBACK
            runtime = build_runtime_segment(
                mode,
                fallback_reason=runtime.get("fallback_reason") or ("MANUAL" if mode == RuntimeMode.DETERMINISTIC_FALLBACK else None),
                supervisor_version=decision.supervisor_version,
            )
        runtime.setdefault("supervisor_version", decision.supervisor_version)
        # 详细修改方案 §5：proposal / policy / tools / inputs / output 段。
        decision_action = (decision.thesis or {}).get("action") or (decision.portfolio_advice or {}).get("action")
        proposal = dict(provided.get("proposal") or {})
        proposal.setdefault("candidates", [str(item.get("symbol")) for item in (decision.candidates or []) if isinstance(item, dict) and item.get("symbol")])
        proposal.setdefault("action", decision_action)
        policy = dict(provided.get("policy") or {})
        policy.setdefault("policy_version", risk.get("risk_rule_version") or portfolio.get("portfolio_policy_version"))
        policy.setdefault("approved", decision_action not in (None, "REJECT", "VETO"))
        tools = dict(provided.get("tools") or {})
        inputs = dict(provided.get("inputs") or {
            "market_snapshot_ids": [market.get("snapshot_id")] if market.get("snapshot_id") else [],
            "content_snapshot_ids": [content.get("snapshot_id")] if content.get("snapshot_id") else [],
            "research_experiment_ids": [factor.get("research_experiment_id")] if factor.get("research_experiment_id") else [],
            "factor_set_ids": [factor.get("factor_set_version")] if factor.get("factor_set_version") else [],
        })
        output = dict(provided.get("output") or {
            "final_decision": decision_action,
            "portfolio_advice_actions": (decision.portfolio_advice or {}).get("actions"),
            "benchmark_route": (decision.benchmark_route or {}).get("primary_benchmark"),
        })
        lineage = self._derive_lineage(provided.get("lineage") or [], market, content, factor, strategy)
        snapshot_payload = {
            "decision_id": decision.id,
            "decision_time": decision.decision_as_of or decision.created_at,
            "schema_version": DECISION_SNAPSHOT_SCHEMA_V2,
            "market": market,
            "content": content,
            "factor": factor,
            "strategy": strategy,
            "agent": agent,
            "model": model,
            "runtime": runtime,
            "tools": tools,
            "inputs": inputs,
            "proposal": proposal,
            "policy": policy,
            "output": output,
            "portfolio": portfolio,
            "risk": risk,
            "lineage": lineage,
            "decision_quality": decision_quality or provided.get("decision_quality"),
        }
        return self.snapshots.save(**snapshot_payload)

    @staticmethod
    def _derive_lineage(provided: list, market: dict, content: dict, factor: dict, strategy: dict) -> list:
        """收尾文档 §39：自动从各段版本锚点派生 lineage（MARKET_SNAPSHOT / CONTENT_SNAPSHOT / RESEARCH_EXPERIMENT / FACTOR_SET / BACKTEST）。"""
        lineage = [dict(item) for item in provided if isinstance(item, dict)]
        seen = {(item.get("type"), item.get("id")) for item in lineage}

        def _append(entry_type: str, entry_id: Any) -> None:
            if entry_id and (entry_type, entry_id) not in seen:
                lineage.append({"type": entry_type, "id": entry_id})
                seen.add((entry_type, entry_id))

        _append("MARKET_SNAPSHOT", market.get("snapshot_id"))
        _append("CONTENT_SNAPSHOT", content.get("snapshot_id"))
        _append("RESEARCH_EXPERIMENT", factor.get("research_experiment_id"))
        _append("FACTOR_SET", factor.get("factor_set_version"))
        _append("BACKTEST", strategy.get("backtest_id"))
        return lineage

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
