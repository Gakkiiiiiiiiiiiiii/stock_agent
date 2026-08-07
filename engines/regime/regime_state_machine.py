from __future__ import annotations

from datetime import date, datetime
from typing import Any

from engines.market.trading_calendar import normalize_trading_date
from storage.models.research import MarketRegimeState
from storage.repositories.research_repository import MarketRegimeRepository


DEFAULT_CONFIRMATION_DAYS = {"high_position_retreat": 1, "downtrend_market": 2, "crowding_market": 2, "rotation_market": 2, "range_market": 3}
DEFAULT_EMERGENCY_REGIMES = {"high_position_retreat", "downtrend_market"}


class PersistentRegimeStateMachine:
    """Trading-day idempotent regime transitions with confidence-based hysteresis."""

    def __init__(
        self,
        repository: MarketRegimeRepository | None = None,
        confirmation_days: dict[str, int] | None = None,
        min_days_in_regime: int = 2,
        switch_confidence_gap: float = 0.08,
        emergency_regimes: set[str] | None = None,
    ) -> None:
        self.repository = repository or MarketRegimeRepository()
        self.confirmation_days = {**DEFAULT_CONFIRMATION_DAYS, **(confirmation_days or {})}
        self.min_days_in_regime = min_days_in_regime
        self.switch_confidence_gap = switch_confidence_gap
        self.emergency_regimes = emergency_regimes or DEFAULT_EMERGENCY_REGIMES

    def advance(self, market_code: str, candidate_regime: str, as_of: date | datetime, confidence: float | None = None, features: dict[str, Any] | None = None, transition_reason: dict[str, Any] | None = None) -> dict[str, Any]:
        trade_date = normalize_trading_date(as_of)
        state = self.repository.get_state(market_code)
        evidence = transition_reason or {}
        if state is None:
            state = MarketRegimeState(market_code=market_code, confirmed_regime=candidate_regime, confirmed_since=trade_date, candidate_days=0, confirmed_days=1, last_evaluated_date=trade_date, confidence=confidence, features=features or {}, transition_reason={"reason": "initial_classification", **evidence})
            self.repository.save_state(state)
            self.repository.add_history(market_code=market_code, previous_regime=None, new_regime=candidate_regime, started_at=trade_date, ended_at=None, confidence=confidence, evidence=features or {})
            return self._response(None, state, candidate_regime, "stable", True)

        previous = state.confirmed_regime
        state.features = features or {}
        same_day = state.last_evaluated_date == trade_date
        if same_day:
            state.transition_reason = {"reason": "same_trading_day_evidence_update", **evidence}
            self.repository.save_state(state)
            return self._response(previous, state, state.candidate_regime or candidate_regime, "stable" if candidate_regime == previous else "watch_switch", False)

        state.last_evaluated_date = trade_date
        state.confirmed_days = int(state.confirmed_days or 1) + 1
        if candidate_regime == previous:
            state.candidate_regime = None
            state.candidate_since = None
            state.candidate_days = 0
            state.confidence = confidence
            state.transition_reason = {"reason": "confirmed_regime_reaffirmed", **evidence}
            self.repository.save_state(state)
            return self._response(previous, state, candidate_regime, "stable", False)

        if candidate_regime == state.candidate_regime:
            state.candidate_days += 1
        else:
            state.candidate_regime, state.candidate_since, state.candidate_days = candidate_regime, trade_date, 1
        required_days = self.confirmation_days.get(candidate_regime, 2)
        emergency = candidate_regime in self.emergency_regimes
        confidence_ok = emergency or confidence is None or state.confidence is None or confidence >= float(state.confidence) + self.switch_confidence_gap
        duration_ok = emergency or int(state.confirmed_days or 1) >= self.min_days_in_regime
        if state.candidate_days >= required_days and confidence_ok and duration_ok:
            self.repository.close_active_history(market_code, trade_date)
            state.confirmed_regime, state.confirmed_since, state.confidence = candidate_regime, trade_date, confidence
            state.candidate_regime, state.candidate_since, state.candidate_days, state.confirmed_days = None, None, 0, 1
            state.transition_reason = {"reason": "emergency_transition" if emergency else "confirmation_threshold_met", "confirmation_days": required_days, **evidence}
            self.repository.save_state(state)
            self.repository.add_history(market_code=market_code, previous_regime=previous, new_regime=candidate_regime, started_at=trade_date, ended_at=None, confidence=confidence, evidence=features or {})
            return self._response(previous, state, candidate_regime, "confirmed_switch", True)
        state.transition_reason = {"reason": "watch_switch", "confirmation_days": required_days, "confidence_ok": confidence_ok, "duration_ok": duration_ok, **evidence}
        self.repository.save_state(state)
        return self._response(previous, state, candidate_regime, "watch_switch", False)

    @staticmethod
    def _response(previous: str | None, state: MarketRegimeState, candidate: str, status: str, switched: bool) -> dict[str, Any]:
        return {"previous_regime": previous, "candidate_regime": candidate, "confirmed_regime": state.confirmed_regime, "confirmed_since": state.confirmed_since.isoformat(), "switch_status": status, "candidate_days": state.candidate_days, "confirmed_days": state.confirmed_days, "last_evaluated_date": state.last_evaluated_date.isoformat() if state.last_evaluated_date else None, "switched": switched}


def resolve_regime_transition(previous_regime: str | None, candidate_regime: str) -> dict[str, Any]:
    if previous_regime is None or previous_regime == candidate_regime:
        return {"previous_regime": previous_regime, "candidate_regime": candidate_regime, "confirmed_regime": candidate_regime, "switch_status": "stable", "candidate_days": 0}
    return {"previous_regime": previous_regime, "candidate_regime": candidate_regime, "confirmed_regime": previous_regime, "switch_status": "watch_switch", "candidate_days": 1}
