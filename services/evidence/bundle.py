from __future__ import annotations

from datetime import UTC, datetime

from contracts.decision_input import DecisionInputBundle, DecisionInputBundlePatch, apply_bundle_patch, build_bundle
from contracts.evidence import Evidence


class DecisionInputBundleBuilder:
    def __init__(self, *, clock=None) -> None:
        self.clock = clock

    def build(self, **kwargs) -> DecisionInputBundle:
        if self.clock is None:
            now = datetime.now(UTC)
        else:
            now = self.clock() if callable(self.clock) else self.clock.now()
        kwargs.setdefault("created_at", now)
        kwargs.setdefault("decision_time", kwargs["created_at"])
        return build_bundle(**kwargs)


class DecisionInputBundleResolver:
    def __init__(self, *, clock=None) -> None:
        self.clock = clock

    def patch(self, bundle: DecisionInputBundle, *, reason: str, evidence: list[Evidence], created_at: datetime | None = None) -> tuple[DecisionInputBundle, DecisionInputBundlePatch]:
        if created_at is None:
            if self.clock is None:
                raise ValueError("patch created_at must be supplied by an injected clock")
            created_at = self.clock() if callable(self.clock) else self.clock.now()
        return apply_bundle_patch(bundle, reason=reason, evidence=evidence, created_at=created_at)


__all__ = ["DecisionInputBundleBuilder", "DecisionInputBundleResolver"]
