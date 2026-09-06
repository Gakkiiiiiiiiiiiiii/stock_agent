import pytest

from app.application.readiness.formal_policy import FormalReadinessPolicy
from app.application.readiness.service import FormalReadinessService
from app.ports.upstream_capabilities import CapabilityStatus, StaticCapabilityProbe


def test_formal_readiness_fails_closed_when_any_upstream_is_stale():
    probes = {
        "quant": StaticCapabilityProbe(CapabilityStatus("quant", True, "market-data.v1", 1, "PASS", True)),
        "stock_factor": StaticCapabilityProbe(CapabilityStatus("stock_factor", True, "factor.v1", 301, "PASS", True)),
        "stock_content": StaticCapabilityProbe(CapabilityStatus("stock_content", True, "content.v1", 1, "PASS", True)),
    }
    result = FormalReadinessService(probes, FormalReadinessPolicy(max_snapshot_age_seconds=300)).check()
    assert not result.ready
    assert result.reason_codes == ("SNAPSHOT_STALE",)


def test_analysis_readiness_can_be_degraded_but_is_non_authoritative():
    result = FormalReadinessService({}).check()
    assert not result.ready
    assert "CAPABILITY_PROBE_MISSING" in result.reason_codes


def test_formal_readiness_fails_closed_for_mismatched_capability_identity():
    probes = {
        "quant": StaticCapabilityProbe(CapabilityStatus("stock_factor", True, "market-data.v1", 1, "PASS", True)),
        "stock_factor": StaticCapabilityProbe(CapabilityStatus("stock_factor", True, "factor.v1", 1, "PASS", True)),
        "stock_content": StaticCapabilityProbe(CapabilityStatus("stock_content", True, "content.v1", 1, "PASS", True)),
    }
    result = FormalReadinessService(probes).check()
    assert not result.ready
    assert result.reason_codes == ("CAPABILITY_IDENTITY_MISMATCH",)


def test_formal_readiness_fails_closed_for_mismatched_contract_reference():
    probes = {
        "quant": StaticCapabilityProbe(CapabilityStatus("quant", True, "market-data.v2", 1, "PASS", True)),
        "stock_factor": StaticCapabilityProbe(CapabilityStatus("stock_factor", True, "factor.v1", 1, "PASS", True)),
        "stock_content": StaticCapabilityProbe(CapabilityStatus("stock_content", True, "content.v1", 1, "PASS", True)),
    }
    policy = FormalReadinessPolicy(expected_contracts={
        "quant": "market-data.v1", "stock_factor": "factor.v1", "stock_content": "content.v1",
    })
    result = FormalReadinessService(probes, policy).check()
    assert not result.ready
    assert result.reason_codes == ("CONTRACT_MISMATCH",)


@pytest.mark.parametrize(
    ("invalid_status", "reason_code"),
    [
        (CapabilityStatus("stock_factor", True, "market-data.v1", 1, "PASS", True), "CAPABILITY_IDENTITY_MISMATCH"),
        (CapabilityStatus("quant", True, "market-data.v2", 1, "PASS", True), "CONTRACT_MISMATCH"),
        (CapabilityStatus("quant", True, "market-data.v1", 301, "PASS", True), "SNAPSHOT_STALE"),
        (CapabilityStatus("quant", True, "market-data.v1", 1, "PASS", False), "PIT_UNVERIFIED"),
        (CapabilityStatus("quant", True, "market-data.v1", 1, "FAIL", True), "QUALITY_FAILED"),
    ],
)
def test_failed_capability_is_never_reused_by_timeout(invalid_status, reason_code):
    class TransitioningProbe:
        def __init__(self, valid_status):
            self.valid_status = valid_status
            self.status = valid_status

        def check(self):
            if self.status is None:
                raise TimeoutError
            return self.status

    expected = {
        "quant": "market-data.v1",
        "stock_factor": "factor.v1",
        "stock_content": "content.v1",
    }
    probes = {
        name: TransitioningProbe(CapabilityStatus(name, True, contract, 1, "PASS", True))
        for name, contract in expected.items()
    }
    service = FormalReadinessService(
        probes, FormalReadinessPolicy(expected_contracts=expected),
    )

    assert service.check().ready
    probes["quant"].status = invalid_status
    assert reason_code in service.check().reason_codes
    probes["quant"].status = None
    assert not service.check().ready


def test_formal_readiness_denies_timeout_after_identity_mismatch():
    class TransitioningProbe:
        def __init__(self, component: str, contract: str):
            self.component = component
            self.contract = contract
            self.mode = "valid"

        def check(self):
            if self.mode == "timeout":
                raise TimeoutError
            producer = "stock_factor" if self.mode == "mismatch" else self.component
            return CapabilityStatus(producer, True, self.contract, 1, "PASS", True)

    expected = {
        "quant": "market-data.v1",
        "stock_factor": "factor.v1",
        "stock_content": "content.v1",
    }
    probes = {
        name: TransitioningProbe(name, contract)
        for name, contract in expected.items()
    }
    service = FormalReadinessService(
        probes, FormalReadinessPolicy(expected_contracts=expected),
    )

    assert service.check().ready
    probes["quant"].mode = "mismatch"
    assert "CAPABILITY_IDENTITY_MISMATCH" in service.check().reason_codes

    probes["quant"].mode = "timeout"
    result = service.check()
    assert not result.ready
    assert result.components["quant"]["reason_code"] == "UPSTREAM_TIMEOUT"


def test_deterministic_fixture_is_explicit_and_marked_non_production():
    result = FormalReadinessService({}, FormalReadinessPolicy(
        expected_contracts={"quant": "market-data.v1", "stock_factor": "factor.v1", "stock_content": "content.v1"},
    ), deterministic_fixture=True).check()
    assert result.ready
    assert result.reason_codes == ("DETERMINISTIC_FIXTURE_MODE",)
    assert all(item["reason_code"] == "DETERMINISTIC_FIXTURE" for item in result.components.values())


def test_last_success_cache_expires_closed(monkeypatch):
    from datetime import UTC, datetime, timedelta

    class Flaky:
        def __init__(self, component):
            self.component = component
            self.ok = True

        def check(self):
            if not self.ok:
                raise TimeoutError
            return CapabilityStatus(self.component, True, "market-data.v1", 1, "PASS", True)

    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    probes = {name: Flaky(name) for name in ("quant", "stock_factor", "stock_content")}
    service = FormalReadinessService(
        probes,
        FormalReadinessPolicy(ttl_seconds=30), clock=lambda: now[0],
    )
    # All three components have a successful baseline.
    assert service.check().ready
    for probe in probes.values():
        probe.ok = False
    now[0] += timedelta(seconds=10)
    cached = service.check()
    # A last-success capability is still valid inside the explicit TTL.
    assert cached.ready
    now[0] += timedelta(seconds=30)
    expired = service.check()
    assert not expired.ready
    assert "CAPABILITY_CACHE_EXPIRED" in expired.reason_codes


def test_cached_snapshot_age_includes_age_at_probe_and_cache_elapsed(monkeypatch):
    from datetime import UTC, datetime, timedelta

    class Flaky:
        def __init__(self, component):
            self.component = component
            self.ok = True

        def check(self):
            if not self.ok:
                raise TimeoutError
            return CapabilityStatus(self.component, True, "market-data.v1", 299, "PASS", True)

    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    probes = {name: Flaky(name) for name in ("quant", "stock_factor", "stock_content")}
    service = FormalReadinessService(
        probes,
        FormalReadinessPolicy(max_snapshot_age_seconds=300, ttl_seconds=30),
        clock=lambda: now[0],
    )
    assert service.check().ready
    for probe in probes.values():
        probe.ok = False
    now[0] += timedelta(seconds=2)
    result = service.check()
    assert not result.ready
    assert result.components["quant"]["snapshot_age_seconds"] == 301
    assert "SNAPSHOT_STALE" in result.reason_codes


def test_parallel_mismatch_then_timeout_never_relabels_a_cached_producer():
    from threading import Event, Thread

    started, release = Event(), Event()

    class RacingProbe:
        def __init__(self):
            self.calls = 0

        def check(self):
            self.calls += 1
            if self.calls == 1:
                started.set()
                release.wait(timeout=1)
                return CapabilityStatus("stock_factor", True, "market-data.v1", 1, "PASS", True)
            raise TimeoutError

    expected = {
        "quant": "market-data.v1",
        "stock_factor": "factor.v1",
        "stock_content": "content.v1",
    }
    service = FormalReadinessService(
        {
            "quant": RacingProbe(),
            "stock_factor": StaticCapabilityProbe(CapabilityStatus("stock_factor", True, "factor.v1", 1, "PASS", True)),
            "stock_content": StaticCapabilityProbe(CapabilityStatus("stock_content", True, "content.v1", 1, "PASS", True)),
        },
        FormalReadinessPolicy(expected_contracts=expected),
    )
    first = Thread(target=service.check)
    first.start()
    assert started.wait(timeout=1)
    raced = service.check()
    release.set()
    first.join(timeout=1)
    assert not raced.ready
    assert raced.reason_codes == ("UPSTREAM_TIMEOUT",)

    retry = service.check()
    assert not retry.ready
    assert retry.reason_codes == ("UPSTREAM_TIMEOUT",)
