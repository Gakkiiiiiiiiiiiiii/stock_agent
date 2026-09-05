from __future__ import annotations

from typing import Any, Protocol


class SpecialistRunner(Protocol):
    def run(self, bundle: dict[str, Any]) -> dict[str, Any]: ...


class NoopSpecialistRunner:
    """Explicit empty specialist set for deterministic HOLD/replay paths."""
    def run(self, bundle: dict[str, Any]) -> dict[str, Any]:
        return {"status": "NOT_USED", "bundle_hash": bundle.get("bundle_hash")}
