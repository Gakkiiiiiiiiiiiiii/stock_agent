from datetime import UTC, datetime, timedelta

import pytest

from contracts.decision_input import apply_bundle_patch, build_bundle
from contracts.evidence import Evidence, EvidenceQuality, EvidenceType, SourceSystem


def _evidence(available_at):
    return Evidence(evidence_type=EvidenceType.MARKET_SNAPSHOT, source_system=SourceSystem.QUANT, subject_type="market", subject_key="CN_A", as_of=available_at, available_at=available_at, contract_version="v1", payload={"x": 1}, quality_status=EvidenceQuality.VERIFIED)


def test_bundle_hash_cutoff_and_patch_immutability():
    now = datetime.now(UTC)
    bundle = build_bundle(created_at=now, decision_time=now, task_type="daily", objective="test", evidence=[_evidence(now)])
    extra = Evidence(evidence_type=EvidenceType.MARKET_REGIME, source_system=SourceSystem.QUANT, subject_type="market", subject_key="CN_A", as_of=now, available_at=now, contract_version="v1", payload={"regime": "risk_on"}, quality_status=EvidenceQuality.PARTIAL)
    updated, patch = apply_bundle_patch(bundle, reason="required evidence", evidence=[extra], created_at=now + timedelta(seconds=1))
    assert updated.bundle_hash != bundle.bundle_hash
    assert patch.previous_hash == bundle.bundle_hash
    with pytest.raises(ValueError):
        build_bundle(created_at=now, decision_time=now, task_type="daily", objective="test", evidence=[_evidence(now + timedelta(seconds=1))])


def test_patch_with_new_evidence_recomputes_hash_and_rejects_blank_or_lookahead():
    now = datetime.now(UTC)
    bundle = build_bundle(created_at=now, decision_time=now, task_type="daily", objective="test", evidence=[_evidence(now)])
    extra = Evidence(evidence_type=EvidenceType.MARKET_REGIME, source_system=SourceSystem.QUANT, subject_type="market", subject_key="CN_A", as_of=now, available_at=now, contract_version="v1", payload={"regime": "risk_on"}, quality_status=EvidenceQuality.PARTIAL)
    updated, patch = apply_bundle_patch(bundle, reason="regime", evidence=[extra], created_at=now + timedelta(seconds=1))
    assert updated.bundle_hash != bundle.bundle_hash
    assert patch.new_hash == updated.bundle_hash
    with pytest.raises(ValueError):
        apply_bundle_patch(bundle, reason=" ", evidence=[], created_at=now + timedelta(seconds=1))
    with pytest.raises(ValueError, match="no-op"):
        apply_bundle_patch(bundle, reason="duplicate", evidence=[_evidence(now)], created_at=now + timedelta(seconds=1))
    with pytest.raises(ValueError, match="look-ahead"):
        apply_bundle_patch(bundle, reason="future", evidence=[_evidence(now + timedelta(seconds=1))], created_at=now + timedelta(seconds=1))
