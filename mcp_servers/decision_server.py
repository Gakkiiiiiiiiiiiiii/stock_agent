from __future__ import annotations

from datetime import date

from engines.decision.decision_service import DecisionService


def save_investment_decision(**payload) -> dict:
    return DecisionService().save_decision(**payload)


def get_decision(decision_id: str) -> dict:
    return DecisionService().get_decision(decision_id)


def record_decision_outcome(decision_id: str, evaluation_date: str, horizon_days: int, **payload) -> dict:
    return DecisionService().record_outcome(decision_id, date.fromisoformat(evaluation_date), horizon_days, **payload)


def get_decision_outcome(decision_id: str, horizon_days: int | None = None) -> dict:
    return DecisionService().get_outcome(decision_id, horizon_days)


def review_investment_decision(decision_id: str, review: dict, outcome_id: int | None = None) -> dict:
    return DecisionService().review(decision_id, review, outcome_id)
