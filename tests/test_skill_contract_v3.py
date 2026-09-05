from datetime import UTC, datetime

from agent.contracts import SpecialistArtifact
from app.skill_contract import SkillContractValidator, SkillExecutionState
from app.skill_loader import SkillDefinition
from contracts.decision_input import build_bundle
from contracts.evidence import Evidence, EvidenceQuality, EvidenceType, SourceSystem


def test_v3_validates_evidence_specialist_and_governance():
    skill = SkillDefinition(slug="v3", name="v3", description="", version=3, required_evidence=["MARKET_SNAPSHOT"], required_specialists=["MARKET"], governance={"require_risk": True, "require_policy": True})
    state = SkillExecutionState(skill_slug="v3")
    assert "MISSING_REQUIRED_EVIDENCE:MARKET_SNAPSHOT" in SkillContractValidator().validate(skill, state, "")
    state.record_evidence("MARKET_SNAPSHOT")
    state.record_specialist("MARKET")
    state.risk_governed = True
    state.policy_governed = True
    assert "MISSING_REQUIRED_EVIDENCE:MARKET_SNAPSHOT" not in SkillContractValidator().validate(skill, state, "")


def test_v3_runtime_state_is_materialized_from_bundle_artifacts_and_governance():
    now = datetime.now(UTC)
    evidence = [Evidence(evidence_type=kind, source_system=SourceSystem.QUANT, subject_type="market", subject_key="CN_A", as_of=now, available_at=now, contract_version="market.v1", payload={}, quality_status=EvidenceQuality.VERIFIED) for kind in (EvidenceType.MARKET_SNAPSHOT, EvidenceType.MARKET_REGIME)]
    bundle = build_bundle(created_at=now, decision_time=now, task_type="daily", objective="x", evidence=evidence)
    roles = [SpecialistArtifact(task_id="t", specialist=role, evidence_refs=[item.evidence_id for item in evidence]) for role in ("MARKET", "RESEARCH", "TECHNICAL", "FACTOR", "PORTFOLIO", "RISK")]
    skill = SkillDefinition(slug="v3", name="v3", description="", version=3, required_evidence=["MARKET_SNAPSHOT", "MARKET_REGIME"], required_specialists=[role for role in ("MARKET", "RESEARCH", "TECHNICAL", "FACTOR", "PORTFOLIO", "RISK")], governance={"require_risk": True, "require_policy": True}, freshness={"market": {"max_age_minutes": 30}})
    state = SkillExecutionState(skill_slug="v3")
    state.record_bundle(bundle, roles, risk_assessment={"status": "PASS"}, policy_evaluation={"decision": "ALLOW"})
    assert SkillContractValidator().validate(skill, state, "", now=now) == []

    missing = SkillExecutionState(skill_slug="v3")
    missing.record_bundle(bundle, roles[:-1], risk_assessment=None, policy_evaluation=None)
    violations = SkillContractValidator().validate(skill, missing, "", now=now)
    assert "MISSING_REQUIRED_SPECIALIST:RISK" in violations
    assert "RISK_GOVERNANCE_REQUIRED" in violations
    assert "POLICY_GOVERNANCE_REQUIRED" in violations
