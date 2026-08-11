"""Evidence-weighted memory confidence (Memory Lifecycle v2).

Replaces the legacy consecutive +/- outcome counters with a weighted aggregate
over structured ``memory_evidence`` rows. All functions are deterministic: the
same evidence list, config and ``now`` always yield the same result.

Evidence direction (sign of support):
    +1  market_excess_return >= +significance_threshold
    -1  market_excess_return <= -significance_threshold
     0  |excess| inside the noise band, or excess missing (neutral evidence)

Evidence weight:
    weight = quality * applicability * significance * recency
    quality       = clamp(decision_quality, 0, 1), default 0.5 when missing
    applicability = clamp(applicability, 0, 1), default 1.0 when missing
    significance  = min(1, |excess| / significance_threshold), 1.0 when excess
                    is missing or the threshold is not positive
    recency       = 0.5 ** (age_days / recency_half_life_days), 1.0 when no
                    created_at or a non-positive half-life

Weighted confidence (Laplace-smoothed signed aggregation, always in [0, 1]):

    confidence = (1 + sum(direction_i * weight_i)) / (2 + sum(weight_i))

Neutral evidence (direction 0) only grows the denominator, diluting confidence
towards the 0.5 prior. An empty evidence list yields exactly 0.5.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from financial_agent.config import load_yaml_config

DEFAULT_LIFECYCLE_CONFIG: dict[str, Any] = {
    "validate_confidence": 0.7,
    "revalidate_confidence": 0.3,
    "min_evidence_count": 3,
    "significance_threshold": 0.01,
    "recency_half_life_days": 180,
}

_DEFAULT_MEMORY_CONFIG: dict[str, Any] = {
    "lifecycle": DEFAULT_LIFECYCLE_CONFIG,
    "scope": {"overfetch_factor": 3},
    "scorer": {"confidence_bonus_enabled": True, "confidence_bonus_weight": 0.05},
}


def load_memory_config() -> dict:
    """Load the ``memory`` section of config/memory.yaml, merged over defaults."""
    try:
        raw = load_yaml_config("memory.yaml").get("memory") or {}
    except FileNotFoundError:
        raw = {}
    merged = {section: dict(values) for section, values in _DEFAULT_MEMORY_CONFIG.items()}
    for section, values in raw.items():
        if isinstance(values, dict) and isinstance(merged.get(section), dict):
            merged[section] |= values
        else:
            merged[section] = values
    return merged


def _field(evidence: Any, name: str) -> Any:
    if isinstance(evidence, dict):
        return evidence.get(name)
    return getattr(evidence, name, None)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def evidence_direction(evidence: Any, config: dict | None = None) -> int:
    """Sign of support: +1 support / -1 against / 0 neutral (noise band or missing)."""
    threshold = float((config or {}).get("significance_threshold", DEFAULT_LIFECYCLE_CONFIG["significance_threshold"]))
    excess = _field(evidence, "market_excess_return")
    if excess is None:
        return 0
    excess = float(excess)
    if threshold > 0 and abs(excess) < threshold:
        return 0
    return 1 if excess > 0 else (-1 if excess < 0 else 0)


def evidence_weight(evidence: Any, config: dict | None = None, now: datetime | None = None) -> float:
    """Weight of a single evidence event in [0, 1] (see module docstring)."""
    cfg = {**DEFAULT_LIFECYCLE_CONFIG, **(config or {})}
    quality = _field(evidence, "decision_quality")
    quality = _clamp(float(quality)) if quality is not None else 0.5
    applicability = _field(evidence, "applicability")
    applicability = _clamp(float(applicability)) if applicability is not None else 1.0
    excess = _field(evidence, "market_excess_return")
    threshold = float(cfg["significance_threshold"])
    if excess is None or threshold <= 0:
        significance = 1.0
    else:
        significance = min(1.0, abs(float(excess)) / threshold)
    recency = 1.0
    created_at = _field(evidence, "created_at")
    half_life = float(cfg.get("recency_half_life_days") or 0)
    if created_at is not None and half_life > 0:
        moment = now or datetime.now(UTC)
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        age_days = max((moment - created_at).total_seconds() / 86400.0, 0.0)
        recency = 0.5 ** (age_days / half_life)
    return quality * applicability * significance * recency


def weighted_confidence(evidence_list: list, config: dict | None = None, now: datetime | None = None) -> float:
    """Signed weighted aggregation into [0, 1]; 0.5 with no evidence."""
    if not evidence_list:
        return 0.5
    signed = 0.0
    total = 0.0
    for evidence in evidence_list:
        weight = evidence_weight(evidence, config, now)
        total += weight
        signed += evidence_direction(evidence, config) * weight
    return _clamp((1.0 + signed) / (2.0 + total))
