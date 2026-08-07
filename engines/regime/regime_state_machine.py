from __future__ import annotations

from datetime import date, datetime
from typing import Any

from storage.models.research import MarketRegimeState
from storage.repositories.research_repository import MarketRegimeRepository


DEFAULT_CONFIRMATION_DAYS = {
    "high_position_retreat": 1,
    "downtrend_market": 2,
    "crowding_market": 2,
    "rotation_market": 2,
    "range_market": 3,
}


class PersistentRegimeStateMachine:
    """Applies confirmation-day hysteresis and records every confirmed transition."""

    def __init__(self, repository: MarketRegimeRepository | None = None, confirmation_days: dict[str, int] | None = None) -> None:
        self.repository = repository or MarketRegimeRepository()
        self.confirmation_days = {**DEFAULT_CONFIRMATION_DAYS, **(confirmation_days or {})}

    def advance(
        self,
        market_code: str,
        candidate_regime: str,
        as_of: date | datetime,
        confidence: float | None = None,
        features: dict[str, Any] | None = None,
        transition_reason: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trade_date = as_of.date() if isinstance(as_of, datetime) else as_of
        state = self.repository.get_state(market_code)
        evidence = transition_reason or {}
        if state is None:
            created = MarketRegimeState(
                market_code=market_code,
                confirmed_regime=candidate_regime,
                confirmed_since=trade_date,
                candidate_regime=None,
                candidate_since=None,
                candidate_days=0,
                confidence=confidence,
                features=features or {},
                transition_reason={"reason": "initial_classification", **evidence},
            )
            self.repository.save_state(created)
            self.repository.add_history(
                market_code=market_code, previous_regime=None, new_regime=candidate_regime, started_at=trade_date,
                ended_at=None, confidence=confidence, evidence=features or {},
            )
            return self._response(None, created, candidate_regime, "stable", switched=True)

        previous = state.confirmed_regime
        state.confidence = confidence
        state.features = features or {}
        state.transition_reason = evidence
        if candidate_regime == previous:
            state.candidate_regime = None
            state.candidate_since = None
            state.candidate_days = 0
            self.repository.save_state(state)
            return self._response(previous, state, candidate_regime, "stable", switched=False)

        if candidate_regime == state.candidate_regime:
            state.candidate_days += 1
        else:
            state.candidate_regime = candidate_regime
            state.candidate_since = trade_date
            state.candidate_days = 1
        required_days = self.confirmation_days.get(candidate_regime, 2)
        if state.candidate_days >= required_days:
            state.confirmed_regime = candidate_regime
            state.confirmed_since = trade_date
            state.candidate_regime = None
            state.candidate_since = None
            state.candidate_days = 0
            state.transition_reason = {"reason": "confirmation_threshold_met", "confirmation_days": required_days, **evidence}
            self.repository.save_state(state)
            self.repository.add_history(
                market_code=market_code, previous_regime=previous, new_regime=candidate_regime, started_at=trade_date,
                ended_at=None, confidence=confidence, evidence=features or {},
            )
            return self._response(previous, state, candidate_regime, "confirmed_switch", switched=True)
        self.repository.save_state(state)
        return self._response(previous, state, candidate_regime, "watch_switch", switched=False)

    @staticmethod
    def _response(previous: str | None, state: MarketRegimeState, candidate: str, status: str, switched: bool) -> dict[str, Any]:
        return {
            "previous_regime": previous,
            "candidate_regime": candidate,
            "confirmed_regime": state.confirmed_regime,
            "confirmed_since": state.confirmed_since.isoformat(),
            "switch_status": status,
            "candidate_days": state.candidate_days,
            "switched": switched,
        }


def resolve_regime_transition(previous_regime: str | None, candidate_regime: str) -> dict[str, Any]:
    """Backward-compatible stateless transition helper for callers without a store."""
    if previous_regime is None or previous_regime == candidate_regime:
        return {
            "previous_regime": previous_regime,
            "candidate_regime": candidate_regime,
            "confirmed_regime": candidate_regime,
            "switch_status": "stable",
            "candidate_days": 0,
        }
    return {
        "previous_regime": previous_regime,
        "candidate_regime": candidate_regime,
        "confirmed_regime": previous_regime,
        "switch_status": "watch_switch",
        "candidate_days": 1,
    }
