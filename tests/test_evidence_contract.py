from datetime import UTC, datetime

import pytest

from contracts.evidence import Evidence, EvidenceQuality, EvidenceType, SourceSystem


def test_evidence_id_is_stable_and_payload_order_independent():
    now = datetime(2026, 8, 24, 9, 30, tzinfo=UTC)
    a = Evidence(evidence_type=EvidenceType.MARKET_SNAPSHOT, source_system=SourceSystem.QUANT, subject_type="market", subject_key="CN_A", as_of=now, available_at=now, contract_version="market.v1", payload={"b": 2, "a": 1}, quality_status=EvidenceQuality.VERIFIED)
    b = Evidence(evidence_type=EvidenceType.MARKET_SNAPSHOT, source_system=SourceSystem.QUANT, subject_type="market", subject_key="CN_A", as_of=now, available_at=now, contract_version="market.v1", payload={"a": 1, "b": 2}, quality_status=EvidenceQuality.VERIFIED)
    assert a.evidence_id == b.evidence_id
    with pytest.raises(Exception):
        a.source_system = SourceSystem.FACTOR


def test_decision_memory_cannot_be_external_fact():
    now = datetime.now(UTC)
    with pytest.raises(ValueError):
        Evidence(evidence_type=EvidenceType.DECISION_MEMORY, source_system=SourceSystem.CONTENT, subject_type="memory", subject_key="m", as_of=now, available_at=now, contract_version="v1", payload={}, quality_status=EvidenceQuality.VERIFIED)


def test_evidence_rejects_forged_id_non_json_and_nested_mutation():
    now = datetime.now(UTC)
    kwargs = dict(evidence_type=EvidenceType.MARKET_SNAPSHOT, source_system=SourceSystem.QUANT, subject_type="market", subject_key="CN_A", as_of=now, available_at=now, contract_version="market.v1", payload={"nested": [1]}, quality_status=EvidenceQuality.VERIFIED)
    item = Evidence(**kwargs)
    with pytest.raises(ValueError, match="evidence_id"):
        Evidence(evidence_id="ev-quant-market_snapshot-forged", **kwargs)
    with pytest.raises(ValueError, match="non-JSON"):
        Evidence(**{**kwargs, "payload": {"bad": object()}})
    with pytest.raises(TypeError):
        item.payload["nested"] += [2]
    with pytest.raises(ValueError):
        Evidence(**{**kwargs, "unexpected_contract_field": True})
