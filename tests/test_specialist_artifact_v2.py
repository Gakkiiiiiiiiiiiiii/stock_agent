import pytest

from agent.contracts import AgentRole, SpecialistArtifact, SpecialistRole, SpecialistStatus, ToolUsage


def test_specialist_artifact_has_explicit_unknowns_and_stable_hash():
    artifact = SpecialistArtifact(task_id="t1", specialist=AgentRole.RISK, status=SpecialistStatus.DEGRADED, unknowns=["RISK_FAILED"])
    assert artifact.artifact_hash
    assert artifact.unknowns == ["RISK_FAILED"]
    assert artifact.agent == AgentRole.RISK


@pytest.mark.parametrize("role", list(SpecialistRole))
def test_all_specialist_roles_have_v2_fields_and_deep_immutable_hash(role):
    artifact = SpecialistArtifact(task_id="t1", specialist=role, conclusion={"evidence": [1]}, evidence_refs=["ev-1"], unknowns=[], tool_usage=ToolUsage(calls=1, tool_names=["read_evidence"]))
    assert artifact.specialist == role
    assert artifact.evidence_refs == ["ev-1"]
    assert artifact.artifact_hash
    with pytest.raises(TypeError):
        artifact.conclusion["evidence"] += [2]
    with pytest.raises(TypeError):
        artifact.tool_usage.tool_names += ["write"]
