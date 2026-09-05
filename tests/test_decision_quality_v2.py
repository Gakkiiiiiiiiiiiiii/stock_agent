from datetime import UTC, datetime

from agent.contracts import SpecialistArtifact, SpecialistRole, SpecialistStatus
from agent.decision_quality import DecisionQuality, compute_decision_quality_v2
from contracts.evidence import Evidence, EvidenceQuality, EvidenceType, SourceSystem


def _market(quality=EvidenceQuality.VERIFIED):
    now = datetime.now(UTC)
    return Evidence(evidence_type=EvidenceType.MARKET_SNAPSHOT, source_system=SourceSystem.QUANT, subject_type="market", subject_key="CN_A", as_of=now, available_at=now, contract_version="market.v1", payload={}, quality_status=quality)


def test_quality_hard_rules_and_real_scores():
    market = _market()
    assessment = compute_decision_quality_v2(coverage={"market": 1}, evidence=[market], target_weight_requested=True, specialist_artifacts=[SpecialistArtifact(task_id="t", specialist=SpecialistRole.RISK, status=SpecialistStatus.FAILED, unknowns=["RISK_FAILED"])], conflicts=[{"dimension": "regime"}])
    assert assessment.level == DecisionQuality.DEGRADED
    assert not assessment.actionable
    assert "RISK_FAILED" in assessment.degraded_reasons
    assert assessment.specialist_completion_score == 0
    assert assessment.conflict_score < 1


def test_all_unverified_is_low_and_market_missing_is_degraded():
    uncertain = _market(EvidenceQuality.UNVERIFIED)
    low = compute_decision_quality_v2(coverage={"market": 1}, evidence=[uncertain])
    assert low.level == DecisionQuality.LOW
    missing = compute_decision_quality_v2(coverage={"market": 0})
    assert missing.level == DecisionQuality.DEGRADED
    assert not missing.actionable
