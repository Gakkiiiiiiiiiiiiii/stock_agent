from datetime import UTC, datetime, timedelta

import pytest

from contracts.evidence import EvidenceType
from services.evidence.gateway import EvidenceGateway
from services.evidence.validators import validate_available_at


def test_gateway_deduplicates_and_rejects_lookahead():
    now = datetime.now(UTC)
    gateway = EvidenceGateway()
    requests = [{"source_system": "quant", "evidence_type": EvidenceType.MARKET_SNAPSHOT.value, "subject_key": "CN_A", "payload": {"x": 1}, "as_of": now, "available_at": now}]
    evidence, statuses = gateway.collect(requests + requests, decision_time=now, trusted_payload=True)
    assert len(evidence) == 1
    assert statuses[0].status.value == "DEGRADED"
    with pytest.raises(ValueError):
        validate_available_at(evidence[0], now - timedelta(seconds=1))


class _Quant:
    def __init__(self, now):
        self.now = now
        self.calls = []

    def __getattr__(self, name):
        def call(**kwargs):
            self.calls.append((name, kwargs))
            return {"as_of": self.now.isoformat(), "available_at": self.now.isoformat(), "service_version": "q-1", "snapshot_id": "snap-1"}
        return call


class _Factor:
    def get_factor(self, **kwargs):
        return {"as_of": NOW, "available_at": NOW, "factor_set_version": "f-1"}

    get_factor_evidence = get_factor

    def list_factors(self, **kwargs):
        return {"as_of": NOW, "available_at": NOW, "items": []}


class _Content:
    def search_video_knowledge(self, **kwargs):
        return {"as_of": NOW, "available_at": NOW, "items": []}

    def get_knowledge_unit(self, **kwargs):
        return {"as_of": NOW, "available_at": NOW, "claim": "x"}


NOW = datetime.now(UTC)


def test_gateway_allowlisted_dispatch_metadata_clock_and_source_ref():
    quant = _Quant(NOW)
    gateway = EvidenceGateway(quant_client=quant, factor_client=_Factor(), content_client=_Content(), clock=lambda: NOW)
    requests = [
        {"source_system": "quant", "operation": "market_snapshot", "evidence_type": "MARKET_SNAPSHOT", "subject_key": "CN_A"},
        {"source_system": "quant", "operation": "market_regime", "evidence_type": "MARKET_REGIME", "subject_key": "CN_A"},
        {"source_system": "quant", "operation": "sector_strength", "evidence_type": "SECTOR_STRENGTH", "subject_key": "CN_A"},
        {"source_system": "quant", "operation": "technical_evidence", "evidence_type": "TECHNICAL_SIGNAL", "subject_key": "600000.SH"},
        {"source_system": "quant", "operation": "portfolio_snapshot", "evidence_type": "PORTFOLIO_POSITION", "subject_key": "portfolio"},
        {"source_system": "quant", "operation": "backtest", "evidence_type": "BACKTEST_RESULT", "subject_key": "bt-1"},
    ]
    evidence, statuses = gateway.collect(requests, decision_time=NOW)
    assert len(evidence) == len(requests)
    assert all(item.source_ref.startswith("quant:") for item in evidence)
    assert all(item.contract_version == "market-data.v1" for item in evidence)
    assert statuses[0].checked_at == NOW
    assert statuses[0].service_version == "q-1"
    assert {name for name, _ in quant.calls} >= {"get_market_snapshot", "get_market_regime", "get_sector_strength", "get_technical_evidence", "get_portfolio_snapshot", "get_backtest"}


def test_snapshot_selector_never_becomes_frozen_snapshot_id():
    quant = _Quant(NOW)
    gateway = EvidenceGateway(quant_client=quant, clock=lambda: NOW)
    evidence, statuses = gateway.collect([
        {"source_system": "quant", "operation": "market_snapshot", "evidence_type": "MARKET_SNAPSHOT", "subject_key": "CN_A", "snapshot_id": "latest"},
    ], decision_time=NOW)
    assert evidence[0].snapshot_id == "snap-1"
    assert statuses[0].snapshot_id == "snap-1"
    assert "latest" not in {evidence[0].snapshot_id, statuses[0].snapshot_id}
    assert dict(quant.calls)["get_market_snapshot"]["snapshot_id"] == "latest"


def test_gateway_rejects_fixture_in_formal_mode_and_mismatched_operations():
    request = {"source_system": "quant", "operation": "market_snapshot", "evidence_type": "MARKET_SNAPSHOT", "as_of": NOW, "available_at": NOW, "payload": {}}
    with pytest.raises(ValueError, match="trusted offline"):
        EvidenceGateway().collect([request], decision_time=NOW)
    with pytest.raises(ValueError):
        EvidenceGateway().collect([{**request, "evidence_type": "FACTOR_SCORE"}], decision_time=NOW, trusted_payload=True)
    with pytest.raises(ValueError):
        EvidenceGateway().collect([{**request, "method": "POST"}], decision_time=NOW, trusted_payload=True)
    _, statuses = EvidenceGateway().collect([{**request, "as_of": NOW.replace(tzinfo=None)}], decision_time=NOW, trusted_payload=True)
    assert statuses[0].status.value == "DEGRADED"


def test_gateway_freshness_is_stale_and_status_not_ok():
    old = NOW - timedelta(days=2)
    request = {"source_system": "quant", "evidence_type": "MARKET_SNAPSHOT", "as_of": old, "available_at": old, "payload": {}, "max_age_seconds": 60}
    evidence, statuses = EvidenceGateway(clock=lambda: NOW).collect([request], decision_time=NOW, trusted_payload=True)
    assert evidence[0].quality_status.value == "STALE"
    assert statuses[0].status.value == "STALE"
    assert "STALE_DATA" in statuses[0].reason_codes


def test_gateway_external_contract_failures_become_dependency_statuses():
    class MissingQuant:
        def get_market_snapshot(self, **kwargs):
            return {}

    missing_times = {"source_system": "quant", "operation": "market_snapshot", "evidence_type": "MARKET_SNAPSHOT"}
    evidence, statuses = EvidenceGateway(quant_client=MissingQuant(), clock=lambda: NOW).collect([missing_times], decision_time=NOW)
    assert evidence == []
    assert statuses[0].status.value == "DEGRADED"
    assert "INVALID_SNAPSHOT" in statuses[0].reason_codes

    wrong_contract = {"source_system": "quant", "operation": "market_snapshot", "evidence_type": "MARKET_SNAPSHOT", "contract_version": "wrong.v9"}
    _, statuses = EvidenceGateway(quant_client=_Quant(NOW), clock=lambda: NOW).collect([wrong_contract], decision_time=NOW)
    assert statuses[0].status.value == "DEGRADED"
    assert "CONTRACT_MISMATCH" in statuses[0].reason_codes

    future = {"source_system": "quant", "operation": "market_snapshot", "evidence_type": "MARKET_SNAPSHOT", "as_of": NOW.isoformat(), "available_at": (NOW + timedelta(minutes=1)).isoformat()}
    _, statuses = EvidenceGateway(quant_client=_Quant(NOW), clock=lambda: NOW).collect([future], decision_time=NOW)
    assert statuses[0].status.value == "STALE"
    assert "STALE_DATA" in statuses[0].reason_codes


def test_gateway_client_timeout_is_explicit_unavailable():
    class TimeoutQuant:
        def get_market_snapshot(self, **kwargs):
            raise TimeoutError("quant timeout")

    _, statuses = EvidenceGateway(quant_client=TimeoutQuant(), clock=lambda: NOW).collect([{"source_system": "quant", "operation": "market_snapshot", "evidence_type": "MARKET_SNAPSHOT"}], decision_time=NOW)
    assert statuses[0].status.value == "UNAVAILABLE"
    assert statuses[0].reason_codes == ["DEPENDENCY_TIMEOUT"]
