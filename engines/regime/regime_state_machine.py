from __future__ import annotations


def resolve_regime_transition(previous_regime: str | None, candidate_regime: str) -> dict:
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

