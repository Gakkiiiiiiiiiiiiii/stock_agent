from __future__ import annotations

from datetime import date, datetime

from engines.decision.decision_service import DecisionService
from engines.market.market_clock import MarketClock
from storage.repositories.research_repository import MarketRegimeRepository


class DecisionReviewRunner:
    """Domain review orchestration; workers delegate here instead of inventing lessons."""

    def __init__(self, service: DecisionService | None = None, regime_repository: MarketRegimeRepository | None = None, review_agent=None) -> None:
        self.service = service or DecisionService()
        self.regime_repository = regime_repository or MarketRegimeRepository()
        self.review_agent = review_agent

    def run(self, decision_id: str, horizon_days: int = 5) -> dict:
        decision_result = self.service.get_decision(decision_id)
        outcome_result = self.service.get_outcome(decision_id, horizon_days)
        if not decision_result.get("found") or not outcome_result.get("found"):
            raise ValueError("DECISION_OR_OUTCOME_NOT_FOUND")
        decision, outcome = decision_result["decision"], outcome_result["outcome"]
        start = self._as_date(decision.get("decision_as_of") or decision.get("created_at"))
        end = outcome["evaluation_date"]
        history = self.regime_repository.list_history("CN_A", start_date=start, end_date=end, limit=100)
        agent = self.review_agent or self._default_review_agent()
        historical_regimes = [self._history_item(item) for item in history]
        if agent is not None and getattr(agent, "configured", lambda: False)():
            response = agent.run(
                f"请复盘决策 {decision_id} 在 T+{horizon_days} 的真实结果，并保存结构化复盘。",
                context={"decision": decision, "outcome": outcome, "regime_history": historical_regimes},
                force_skill="decision-outcome-review",
            )
            saved_call = next(
                (call.get("output") or {} for call in reversed(response.tool_calls) if call.get("name") == "review_investment_decision"),
                None,
            )
            if saved_call and saved_call.get("review_id"):
                model = getattr(getattr(agent, "client", None), "settings", None)
                self.service.annotate_review(
                    int(saved_call["review_id"]),
                    review_mode="skill",
                    review_model=getattr(model, "model", None),
                )
                return saved_call | {"report": response.report, "historical_regimes": historical_regimes, "mode": "skill"}

        review = self._build_review(decision, outcome, history)
        review["outcome_excess_return"] = outcome.get("excess_return")
        review["review_mode"] = "deterministic_fallback"
        review["regime_path"] = historical_regimes
        review["evidence_refs"] = list(decision.get("evidence_refs") or [])
        saved = self.service.review(decision_id, review, outcome["id"])
        return saved | {"review": review, "historical_regimes": historical_regimes, "mode": "deterministic_fallback"}

    @staticmethod
    def _default_review_agent():
        # Keep offline workers deterministic, but do not silently bypass the
        # executable review skill when an agent model has been configured.
        from app.claude_agent import ClaudeAgent

        return ClaudeAgent()

    @staticmethod
    def _build_review(decision: dict, outcome: dict, history: list) -> dict:
        excess = float(outcome.get("excess_return") or 0)
        correct = ["相对基准取得超额收益"] if excess > 0 else []
        wrong = ["候选组合未取得相对基准超额收益"] if excess <= 0 else []
        regimes = [item.new_regime for item in history]
        lesson = "在该市场环境下应降低同类信号权重，并强化证伪条件。" if excess <= 0 else "该信号在此市场环境下有效，继续监控其证伪条件。"
        return {"decision_quality": max(0.0, min(1.0, 0.5 + excess * 5)), "what_was_correct": correct, "what_was_wrong": wrong, "root_causes": ["市场状态路径：" + " → ".join(regimes)] if regimes else ["历史市场状态数据不足"], "unexpected_events": [], "lessons": [lesson], "applicable_regimes": regimes, "invalidation_updates": list(decision.get("invalidation_conditions") or [])}

    @staticmethod
    def _as_date(value) -> date:
        if isinstance(value, date) and not hasattr(value, "hour"):
            return value
        parsed = value if hasattr(value, "tzinfo") else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return MarketClock().calendar_date(parsed)

    @staticmethod
    def _history_item(item) -> dict:
        return {"regime": item.new_regime, "start_date": item.started_at.isoformat(), "end_date": item.ended_at.isoformat() if item.ended_at else None, "confidence": item.confidence}
