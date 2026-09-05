from datetime import UTC, datetime

import pytest

from contracts.decision_input import (
    DecisionInputBundlePatch,
    apply_bundle_patch,
    build_bundle,
)
from contracts.evidence import Evidence, EvidenceQuality, EvidenceType, SourceSystem
from storage.repositories.decision_input_repository import DecisionInputBundleRepository


def _evidence(now):
    return Evidence(
        evidence_type=EvidenceType.MARKET_SNAPSHOT,
        source_system=SourceSystem.QUANT,
        subject_type="market",
        subject_key="CN_A",
        as_of=now,
        available_at=now,
        contract_version="market.v1",
        payload={"close": 1},
        quality_status=EvidenceQuality.VERIFIED,
    )


def test_bundle_patch_round_trip_and_conflict(isolated_database):
    now = datetime.now(UTC)
    bundle = build_bundle(created_at=now, decision_time=now, task_type="daily", objective="review", evidence=[_evidence(now)])
    extra = Evidence(
        evidence_type=EvidenceType.MARKET_REGIME,
        source_system=SourceSystem.QUANT,
        subject_type="market",
        subject_key="CN_A",
        as_of=now,
        available_at=now,
        contract_version="market.v1",
        payload={"regime": "risk_on"},
        quality_status=EvidenceQuality.PARTIAL,
    )
    updated, patch = apply_bundle_patch(bundle, reason="new evidence", evidence=[extra], created_at=now)
    repository = DecisionInputBundleRepository()
    repository.save(bundle)
    row = repository.apply_patch(updated, patch)
    assert row.bundle_hash == updated.bundle_hash
    assert repository.get_patch(patch.patch_id).new_hash == patch.new_hash
    assert len(repository.list_patches(bundle.bundle_id)) == 1
    assert repository.apply_patch(updated, patch).bundle_hash == patch.new_hash
    assert repository.get_bundle(bundle.bundle_id).bundle_hash == updated.bundle_hash
    conflict = DecisionInputBundlePatch(
        patch_id=patch.patch_id,
        reason="different",
        created_at=now,
        evidence=[],
        previous_hash=patch.previous_hash,
        new_hash="0" * 64,
    )
    with pytest.raises(ValueError, match="lineage|conflict"):
        repository.apply_patch(updated, conflict)
