"""Runtime capability selection, resolved before composition imports."""
from __future__ import annotations

import os
from enum import StrEnum


class CapabilityProfile(StrEnum):
    KNOWLEDGE_ONLY = "knowledge-only"
    FULL = "full"


def resolve_capability_profile(value: str | None = None) -> CapabilityProfile:
    """Return the explicit profile, preserving FULL as the legacy default."""

    configured = (value if value is not None else os.getenv("STOCK_AGENT_PROFILE", "full")).strip().lower()
    try:
        return CapabilityProfile(configured)
    except ValueError as exc:
        allowed = ", ".join(profile.value for profile in CapabilityProfile)
        raise RuntimeError(f"invalid STOCK_AGENT_PROFILE={configured!r}; expected one of: {allowed}") from exc
