from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engines.memory.evidence import (
    DEFAULT_LIFECYCLE_CONFIG,
    evidence_direction,
    evidence_weight,
    weighted_confidence,
)

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def _ev(**overrides):
    base = {
        "market_excess_return": 0.02,
        "decision_quality": 0.8,
        "applicability": 1.0,
        "created_at": NOW,
    }
    return base | overrides


def test_evidence_direction_uses_noise_band():
    assert evidence_direction(_ev(market_excess_return=0.02)) == 1
    assert evidence_direction(_ev(market_excess_return=-0.02)) == -1
    assert evidence_direction(_ev(market_excess_return=0.005)) == 0  # inside noise band
    assert evidence_direction(_ev(market_excess_return=0.01)) == 1  # threshold boundary is significant
    assert evidence_direction(_ev(market_excess_return=None)) == 0
    assert evidence_direction(_ev(market_excess_return=0.0)) == 0


def test_evidence_weight_scales_with_quality_and_applicability():
    full = evidence_weight(_ev(), DEFAULT_LIFECYCLE_CONFIG, now=NOW)
    assert full == pytest.approx(0.8)
    assert evidence_weight(_ev(decision_quality=0.5), DEFAULT_LIFECYCLE_CONFIG, now=NOW) == pytest.approx(0.5)
    assert evidence_weight(_ev(applicability=0.5), DEFAULT_LIFECYCLE_CONFIG, now=NOW) == pytest.approx(0.4)
    # Missing quality defaults to 0.5, missing applicability defaults to 1.0.
    assert evidence_weight(_ev(decision_quality=None), DEFAULT_LIFECYCLE_CONFIG, now=NOW) == pytest.approx(0.5)
    assert evidence_weight(_ev(applicability=None), DEFAULT_LIFECYCLE_CONFIG, now=NOW) == pytest.approx(0.8)


def test_evidence_weight_scales_down_below_significance_threshold():
    assert evidence_weight(_ev(market_excess_return=0.005), DEFAULT_LIFECYCLE_CONFIG, now=NOW) == pytest.approx(0.8 * 0.5)
    assert evidence_weight(_ev(market_excess_return=None), DEFAULT_LIFECYCLE_CONFIG, now=NOW) == pytest.approx(0.8)


def test_evidence_weight_decays_with_half_life():
    half_life = DEFAULT_LIFECYCLE_CONFIG["recency_half_life_days"]
    old = _ev(created_at=NOW - timedelta(days=half_life))
    assert evidence_weight(old, DEFAULT_LIFECYCLE_CONFIG, now=NOW) == pytest.approx(0.8 * 0.5)
    older = _ev(created_at=NOW - timedelta(days=2 * half_life))
    assert evidence_weight(older, DEFAULT_LIFECYCLE_CONFIG, now=NOW) == pytest.approx(0.8 * 0.25)


def test_weighted_confidence_bounds_and_prior():
    assert weighted_confidence([], DEFAULT_LIFECYCLE_CONFIG) == 0.5
    supportive = [_ev() for _ in range(3)]
    confidence = weighted_confidence(supportive, DEFAULT_LIFECYCLE_CONFIG, now=NOW)
    assert 0.0 <= confidence <= 1.0
    assert confidence > DEFAULT_LIFECYCLE_CONFIG["validate_confidence"]
    negative = [_ev(market_excess_return=-0.02) for _ in range(3)]
    assert weighted_confidence(negative, DEFAULT_LIFECYCLE_CONFIG, now=NOW) < DEFAULT_LIFECYCLE_CONFIG["revalidate_confidence"]


def test_weighted_confidence_neutral_evidence_dilutes_towards_prior():
    supportive = [_ev() for _ in range(3)]
    neutral = _ev(market_excess_return=0.005)  # noise band: direction 0, halved significance weight
    diluted = weighted_confidence([*supportive, neutral], DEFAULT_LIFECYCLE_CONFIG, now=NOW)
    assert diluted < weighted_confidence(supportive, DEFAULT_LIFECYCLE_CONFIG, now=NOW)
    assert diluted > 0.5


def test_weighted_confidence_is_deterministic():
    events = [_ev(), _ev(market_excess_return=-0.03), _ev(market_excess_return=None, decision_quality=None)]
    first = weighted_confidence(events, DEFAULT_LIFECYCLE_CONFIG, now=NOW)
    second = weighted_confidence(events, DEFAULT_LIFECYCLE_CONFIG, now=NOW)
    assert first == second
