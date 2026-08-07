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

    def list_history(self, market_code: str, limit: int = 30) -> list[MarketRegimeHistory]:
        with session_scope() as session:
            return list(session.execute(
                select(MarketRegimeHistory).where(MarketRegimeHistory.market_code == market_code).order_by(MarketRegimeHistory.started_at.desc()).limit(limit)
            ).scalars())


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

    def add_outcome(self, **payload) -> InvestmentDecisionOutcome:
        with session_scope() as session:
            outcome = InvestmentDecisionOutcome(**payload)
            session.add(outcome)
            session.flush()
            session.refresh(outcome)
            return outcome

    def get_outcome(self, decision_id: str, horizon_days: int | None = None) -> InvestmentDecisionOutcome | None:
        with session_scope() as session:
            query = select(InvestmentDecisionOutcome).where(InvestmentDecisionOutcome.decision_id == decision_id)
            if horizon_days is not None:
                query = query.where(InvestmentDecisionOutcome.horizon_days == horizon_days)
            return session.execute(query.order_by(InvestmentDecisionOutcome.evaluation_date.desc())).scalars().first()

    def add_review(self, **payload) -> DecisionReview:
        with session_scope() as session:
            review = DecisionReview(**payload)
            session.add(review)
            session.flush()
            session.refresh(review)
            return review
