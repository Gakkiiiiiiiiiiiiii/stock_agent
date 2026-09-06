"""Fail-closed formal decision readiness, separate from process liveness."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from threading import RLock

from app.application.readiness.formal_policy import FormalReadinessPolicy
from app.ports.upstream_capabilities import CapabilityStatus, UpstreamCapabilityProbe


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    checked_at: str
    policy_version: str
    components: dict[str, dict]
    reason_codes: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "ready": self.ready, "checked_at": self.checked_at,
            "policy_version": self.policy_version, "components": self.components,
            "reason_codes": list(self.reason_codes),
        }


class FormalReadinessService:
    def __init__(self, probes: Mapping[str, UpstreamCapabilityProbe], policy: FormalReadinessPolicy | None = None, *, clock: Callable[[], datetime] | None = None, deterministic_fixture: bool = False):
        self.probes = dict(probes)
        self.policy = policy or FormalReadinessPolicy()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.deterministic_fixture = deterministic_fixture
        self._cache: dict[str, tuple[CapabilityStatus, datetime]] = {}
        # Probe calls remain concurrent-safe without holding this lock across
        # upstream I/O.  A timeout may only use a complete last-success entry.
        self._cache_lock = RLock()

    def check(self) -> ReadinessResult:
        now = self.clock()
        if self.deterministic_fixture:
            # A fixture is an explicit, non-production capability.  It keeps
            # smoke tests deterministic while the decision route separately
            # forces HOLD/non-executable output in this mode.
            fixture_components = {
                name: {
                    "reachable": True,
                    "contract": self.policy.expected_contracts.get(name),
                    "snapshot_age_seconds": 0.0,
                    "quality": "PASS",
                    "pit": True,
                    "reason_code": "DETERMINISTIC_FIXTURE",
                }
                for name in ("quant", "stock_factor", "stock_content")
            }
            return ReadinessResult(True, now.isoformat(), self.policy.policy_version, fixture_components, ("DETERMINISTIC_FIXTURE_MODE",))
        components: dict[str, dict] = {}
        reasons: list[str] = ["CONTRACT_MANIFEST_INVALID"] if self.policy.manifest_error else []
        for name in ("quant", "stock_factor", "stock_content"):
            probe = self.probes.get(name)
            if probe is None:
                status = CapabilityStatus(name, False, reason_code="CAPABILITY_PROBE_MISSING")
            else:
                try:
                    status = probe.check()
                    if status.reachable:
                        with self._cache_lock:
                            # A last-success entry is evidence used by a later
                            # timeout.  Do not let it turn an observed bad
                            # producer into a different, apparently healthy
                            # component.  Invalidate a prior entry as soon as
                            # a reachable response fails any formal gate.
                            if self._validation_reasons(name, status):
                                self._cache.pop(name, None)
                            else:
                                self._cache[name] = (status, now)
                except (ConnectionError, TimeoutError, OSError):
                    status = self._cached_or_failed(name, now, "UPSTREAM_TIMEOUT")
                except Exception:  # noqa: BLE001 - a probe failure is fail-closed
                    status = self._cached_or_failed(name, now, "UPSTREAM_PROBE_ERROR")
            item = asdict(status)
            item.pop("component", None)
            if status.reason_code == "CACHED_LAST_SUCCESS" and name in self._cache:
                _, cached_at = self._cache[name]
                item["cached_at"] = cached_at.isoformat()
                item["cache_age_seconds"] = max(0.0, (now - cached_at).total_seconds())
            components[name] = item
            if not status.reachable:
                reasons.append(status.reason_code or "UPSTREAM_UNAVAILABLE")
                continue
            reasons.extend(self._validation_reasons(name, status))
        # No missing quality is permitted in formal mode. ``None`` is retained
        # for older probe contracts only when explicitly configured by policy.
        return ReadinessResult(not reasons, now.isoformat(), self.policy.policy_version, components, tuple(dict.fromkeys(reasons)))

    def _validation_reasons(self, name: str, status: CapabilityStatus) -> tuple[str, ...]:
        """Return every formal-gate failure for a reachable capability."""
        reasons: list[str] = []
        if status.component != name:
            reasons.append("CAPABILITY_IDENTITY_MISMATCH")
        if status.contract is None:
            reasons.append("CONTRACT_UNVERIFIED")
        elif (
            self.policy.expected_contracts.get(name)
            and status.contract != self.policy.expected_contracts[name]
        ) or (
            self.policy.required_contracts
            and status.contract not in self.policy.required_contracts
        ):
            reasons.append("CONTRACT_MISMATCH")
        if status.snapshot_age_seconds is None:
            reasons.append("FRESHNESS_UNVERIFIED")
        elif status.snapshot_age_seconds > self.policy.max_snapshot_age_seconds:
            reasons.append("SNAPSHOT_STALE")
        if self.policy.require_pit and status.pit is not True:
            reasons.append("PIT_UNVERIFIED")
        if status.quality != "PASS":
            reasons.append("QUALITY_UNVERIFIED" if status.quality is None else "QUALITY_FAILED")
        return tuple(reasons)

    def _cached_or_failed(self, name: str, now: datetime, reason: str) -> CapabilityStatus:
        with self._cache_lock:
            cached = self._cache.get(name)
        if cached is not None:
            status, stored_at = cached
            age = (now - stored_at).total_seconds()
            if age <= self.policy.ttl_seconds:
                snapshot_age = ((status.snapshot_age_seconds or 0.0) + age
                                 if status.snapshot_age_seconds is not None else None)
                return CapabilityStatus(name, True, status.contract, snapshot_age,
                                        status.quality, status.pit, "CACHED_LAST_SUCCESS")
            snapshot_age = ((status.snapshot_age_seconds or 0.0) + age
                             if status.snapshot_age_seconds is not None else None)
            return CapabilityStatus(name, False, status.contract, snapshot_age,
                                    status.quality, status.pit, "CAPABILITY_CACHE_EXPIRED")
        return CapabilityStatus(name, False, reason_code=reason)
