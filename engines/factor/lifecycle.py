from __future__ import annotations

from enum import Enum


class FactorLifecycleStatus(str, Enum):
    DRAFT = "DRAFT"
    COMPUTABLE = "COMPUTABLE"
    IN_SAMPLE_PASS = "IN_SAMPLE_PASS"
    RECENT_ALPHA = "RECENT_ALPHA"
    OOS_PASS = "OOS_PASS"
    PAPER_TRADING = "PAPER_TRADING"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    RETIRED = "RETIRED"
    LEGACY_UNVERIFIED = "LEGACY_UNVERIFIED"


ACTIVATION_ALLOWED = {FactorLifecycleStatus.APPROVED.value, FactorLifecycleStatus.ACTIVE.value}


def can_activate(status: str) -> bool:
    return status in ACTIVATION_ALLOWED
