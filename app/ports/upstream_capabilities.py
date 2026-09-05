from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CapabilityStatus:
    component: str
    reachable: bool
    contract: str | None = None
    snapshot_age_seconds: float | None = None
    quality: str | None = None
    pit: bool | None = None
    reason_code: str | None = None


class UpstreamCapabilityProbe(Protocol):
    def check(self) -> CapabilityStatus: ...


class StaticCapabilityProbe:
    """Deterministic probe useful for tests and local smoke deployments."""
    def __init__(self, status: CapabilityStatus):
        self.status = status

    def check(self) -> CapabilityStatus:
        return self.status
