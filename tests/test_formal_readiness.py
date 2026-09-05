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
        def __init__(self):
            self.ok = True

        def check(self):
            if not self.ok:
                raise TimeoutError
            return CapabilityStatus("quant", True, "market-data.v1", 1, "PASS", True)

    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    probe = Flaky()
    service = FormalReadinessService(
        {"quant": probe, "stock_factor": probe, "stock_content": probe},
        FormalReadinessPolicy(ttl_seconds=30), clock=lambda: now[0],
    )
    # All three components have a successful baseline.
    assert service.check().ready
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
        def __init__(self):
            self.ok = True

        def check(self):
            if not self.ok:
                raise TimeoutError
            return CapabilityStatus("quant", True, "market-data.v1", 299, "PASS", True)

    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    probe = Flaky()
    service = FormalReadinessService(
        {"quant": probe, "stock_factor": probe, "stock_content": probe},
        FormalReadinessPolicy(max_snapshot_age_seconds=300, ttl_seconds=30),
        clock=lambda: now[0],
    )
    assert service.check().ready
    probe.ok = False
    now[0] += timedelta(seconds=2)
    result = service.check()
    assert not result.ready
    assert result.components["quant"]["snapshot_age_seconds"] == 301
    assert "SNAPSHOT_STALE" in result.reason_codes
