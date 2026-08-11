from __future__ import annotations

from datetime import date

from sqlalchemy import select

from storage.db import session_scope
from storage.models.research import (
    DecisionReview,
    InvestmentDecision,
    InvestmentDecisionOutcome,
    MarketRegimeHistory,
    MarketRegimeState,
)


class MarketRegimeRepository:
    def get_state(self, market_code: str) -> MarketRegimeState | None:
        with session_scope() as session:
            return session.get(MarketRegimeState, market_code)

    def save_state(self, state: MarketRegimeState) -> MarketRegimeState:
        with session_scope() as session:
            session.merge(state)
            session.flush()
            return session.get(MarketRegimeState, state.market_code)

    def add_history(self, **payload) -> MarketRegimeHistory:
        with session_scope() as session:
            item = MarketRegimeHistory(**payload)
            session.add(item)
            session.flush()
            session.refresh(item)
            return item

    def close_active_history(self, market_code: str, ended_at: date) -> None:
        with session_scope() as session:
            active = session.execute(
                select(MarketRegimeHistory).where(MarketRegimeHistory.market_code == market_code, MarketRegimeHistory.ended_at.is_(None)).order_by(MarketRegimeHistory.started_at.desc())
            ).scalars().first()
            if active is not None:
                active.ended_at = ended_at
                session.add(active)

    def list_history(self, market_code: str, limit: int = 30, start_date: date | None = None, end_date: date | None = None) -> list[MarketRegimeHistory]:
        with session_scope() as session:
            query = select(MarketRegimeHistory).where(MarketRegimeHistory.market_code == market_code)
            if start_date:
                query = query.where((MarketRegimeHistory.ended_at.is_(None)) | (MarketRegimeHistory.ended_at >= start_date))
            if end_date:
                query = query.where(MarketRegimeHistory.started_at <= end_date)
            return list(session.execute(query.order_by(MarketRegimeHistory.started_at.desc()).limit(limit)).scalars())


class DecisionRepository:
    def create(self, **payload) -> InvestmentDecision:
        with session_scope() as session:
            decision = InvestmentDecision(**payload)
            session.add(decision)
            session.flush()
            session.refresh(decision)
            return decision

    def get(self, decision_id: str) -> InvestmentDecision | None:
        with session_scope() as session:
            return session.get(InvestmentDecision, decision_id)

    def update(self, decision_id: str, **payload) -> InvestmentDecision:
        with session_scope() as session:
            decision = session.get(InvestmentDecision, decision_id)
            if decision is None:
                raise FileNotFoundError(decision_id)
            for key, value in payload.items():
                setattr(decision, key, value)
            session.add(decision)
            session.flush()
            session.refresh(decision)
            return decision

    def add_outcome(self, **payload) -> InvestmentDecisionOutcome:
        with session_scope() as session:
            outcome = InvestmentDecisionOutcome(**payload)
            session.add(outcome)
            session.flush()
            session.refresh(outcome)
            return outcome

    def list_decision_outcome_rows(self) -> list[dict]:
        """只读：联表 investment_decision × investment_decision_outcome，展开为纯 dict 行。

        供历史校准（engines/regime/calibration.py）使用；仅返回存在
        market_excess_return 且 decision 带有 market_regime 的行。
        """
        with session_scope() as session:
            pairs = session.execute(
                select(InvestmentDecision, InvestmentDecisionOutcome)
                .join(InvestmentDecisionOutcome, InvestmentDecisionOutcome.decision_id == InvestmentDecision.id)
                .where(InvestmentDecision.market_regime.is_not(None))
                .where(InvestmentDecisionOutcome.market_excess_return.is_not(None))
            ).all()
            return [
                {
                    "decision_id": decision.id,
                    "market_regime": decision.market_regime,
                    "skill_slug": decision.skill_slug,
                    "thesis": dict(decision.thesis or {}),
                    "tool_trace": list(decision.tool_trace or []),
                    "themes": list(decision.themes or []),
                    "horizon_days": outcome.horizon_days,
                    "market_excess_return": outcome.market_excess_return,
                    "evaluation_date": outcome.evaluation_date.isoformat() if outcome.evaluation_date else None,
                }
                for decision, outcome in pairs
            ]

    def get_outcome(self, decision_id: str, horizon_days: int | None = None) -> InvestmentDecisionOutcome | None:
        with session_scope() as session:
            query = select(InvestmentDecisionOutcome).where(InvestmentDecisionOutcome.decision_id == decision_id)
            if horizon_days is not None:
                query = query.where(InvestmentDecisionOutcome.horizon_days == horizon_days)
            return session.execute(query.order_by(InvestmentDecisionOutcome.evaluation_date.desc())).scalars().first()

    def get_outcome_by_id(self, outcome_id: int) -> InvestmentDecisionOutcome | None:
        with session_scope() as session:
            return session.get(InvestmentDecisionOutcome, outcome_id)

    def add_review(self, **payload) -> DecisionReview:
        with session_scope() as session:
            review = DecisionReview(**payload)
            session.add(review)
            session.flush()
            session.refresh(review)
            return review

    def update_review(self, review_id: int, **payload) -> DecisionReview:
        with session_scope() as session:
            review = session.get(DecisionReview, review_id)
            if review is None:
                raise FileNotFoundError(review_id)
            for key, value in payload.items():
                setattr(review, key, value)
            session.add(review)
            session.flush()
            session.refresh(review)
            return review
