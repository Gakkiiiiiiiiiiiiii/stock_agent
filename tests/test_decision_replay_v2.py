from __future__ import annotations

from datetime import UTC, datetime

from contracts.decision_input import build_bundle
from contracts.decision_snapshot import DecisionSnapshotV3, SnapshotBundleRef, canonical_hash
from contracts.replay import ReplayMode, ReplayRequest
from engines.decision.replay import DecisionReplayService


NOW = datetime(2026, 8, 20, 9, 30, tzinfo=UTC)


def _snapshot():
    bundle = build_bundle(created_at=NOW, decision_time=NOW, task_type="test", objective="replay")
    payload = bundle.model_dump(mode="json")
    proposal = {"proposal_id": "p-1", "action": "HOLD"}
    policy_material = {"policy_result_id": "r-1", "policy_version": "policy.v1", "checks": [{"rule": "risk", "passed": True}]}
    policy = {**policy_material, "result_hash": canonical_hash(policy_material)}
    final = {"decision_id": "decision-v2", "action": "HOLD", "approved": True, "bundle_id": bundle.bundle_id, "proposal_id": "p-1", "policy_result_id": "r-1"}
    return DecisionSnapshotV3(
        decision_id="decision-v2",
        decision_time=NOW,
        runtime={"runtime_mode": "PRIMARY_AGENT", "workflow_version": "workflow.v1"},
        input_bundle=SnapshotBundleRef(bundle_id=bundle.bundle_id, bundle_hash=bundle.bundle_hash, payload=payload),
        dependencies={"quant": {"status": "OK"}, "factor": {"status": "OK"}, "content": {"status": "OK"}},
        evidence={"refs": [], "hashes": {}, "payloads": {}},
        specialists={"artifact_refs": [], "artifact_hashes": {}, "payloads": {}},
        model={"provider": "test", "model": "old", "model_version": "1", "prompt_version": "1"},
        skill={"slug": "daily", "version": "1", "contract_hash": "skill-hash", "markdown_hash": "markdown-hash"},
        proposal={"proposal_id": proposal["proposal_id"], "proposal_hash": canonical_hash(proposal), "payload": proposal},
        risk={"risk_rule_version": "risk.v1"}, policy=policy,
        output={"decision_id": "decision-v2", "final_decision_id": "f-1", "bundle_id": bundle.bundle_id, "proposal_id": "p-1", "policy_result_id": "r-1", "final_decision": final, "final_decision_hash": canonical_hash(final)},
        lineage=[{"type": "BUNDLE", "id": bundle.bundle_id}, {"type": "PROPOSAL", "id": "p-1", "hash": canonical_hash(proposal)}, {"type": "POLICY", "id": "r-1", "hash": policy["result_hash"]}, {"type": "FINAL_DECISION", "id": "f-1"}, {"type": "DECISION", "id": "decision-v2"}], decision_quality={"level": "HIGH"}, usage={"tool_calls": 0},
    ), bundle


class _Snapshots:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def get_v3_for_decision(self, decision_id):
        return self.snapshot if self.snapshot is not None and decision_id == self.snapshot.decision_id else None


class _Bundles:
    def __init__(self, bundle):
        self.bundle = bundle

    def get_bundle(self, bundle_id):
        return self.bundle if bundle_id == self.bundle.bundle_id else None


def _service(snapshot, bundle, **runners):
    return DecisionReplayService(
        snapshot_repository=_Snapshots(snapshot), bundle_repository=_Bundles(bundle), **runners,
    )


def test_exact_replay_is_snapshot_anchored_and_has_no_changes():
    snapshot, bundle = _snapshot()
    result = _service(snapshot, bundle).replay(snapshot.decision_id, mode="EXACT_REPLAY")
    assert result["status"] == "SUCCEEDED"
    assert result["fixed_fields"] == ["bundle", "evidence", "model_identity", "skill_identity", "policy_version", "workflow_version"]
    assert result["changed_fields"] == []
    assert result["bundle_hash"] == bundle.bundle_hash


def test_each_component_mode_requires_only_its_component_runner():
    snapshot, bundle = _snapshot()
    cases = {
        "MODEL_REPLAY": ("model", {"model": "new"}, "model_runner"),
        "SKILL_REPLAY": ("skill", {"skill": "new"}, "skill_runner"),
        "POLICY_REPLAY": ("policy", {"policy": {"policy_version": "policy.v2"}}, "policy_runner"),
        "WORKFLOW_REPLAY": ("workflow", {"workflow": "new"}, "workflow_runner"),
    }
    for mode, (component, override, runner_name) in cases.items():
        def runner(component=component, **kwargs):
            if component in {"model", "skill"}:
                return {"proposal": {"action": "HOLD"}, "policy_evaluation": {"policy_version": "policy.v1", "checks": []}, "output": {"final_decision": {"action": "HOLD"}}}
            if component == "policy":
                return {"policy_evaluation": {"policy_version": "policy.v2", "checks": []}, "output": {"final_decision": {"action": "HOLD"}}}
            return {"artifacts": {}, "conflicts": [], "proposal": {"action": "HOLD"}, "policy_evaluation": {"policy_version": "policy.v1", "checks": []}, "output": {"final_decision": {"action": "HOLD"}}}

        result = _service(snapshot, bundle, **{runner_name: runner}).replay(snapshot.decision_id, mode=mode, overrides=override)
        assert result["status"] == "SUCCEEDED"
        semantic = {"model": "model_identity", "skill": "skill_identity", "policy": "policy_version", "workflow": "workflow_version"}[component]
        assert semantic in result["changed_fields"]
        assert component not in result["fixed_fields"]
        assert set(result["fixed_fields"]) >= {"bundle", "evidence"}


def test_runner_replacement_is_not_confused_with_emitted_proposal_or_final():
    snapshot, bundle = _snapshot()

    def model_runner(*, model, **kwargs):
        assert model == "new-model"
        return {"proposal": {"action": "BUY", "source": "new-model"}, "policy_evaluation": {"policy_version": "policy.v1", "checks": []}, "output": {"action": "BUY"}}

    result = _service(snapshot, bundle, model_runner=model_runner).replay(
        snapshot.decision_id, mode="MODEL_REPLAY", overrides={"model": "new-model"}
    )
    assert result["status"] == "SUCCEEDED"
    assert result["output"]["replayed"]["model"] == "new-model"
    assert result["output"]["replayed"]["proposal"]["action"] == "BUY"
    assert set(result["changed_fields"]) >= {"model_identity", "proposal_hash", "proposal", "output"}


def test_workflow_runner_can_emit_downstream_artifacts_but_not_change_bundle():
    snapshot, bundle = _snapshot()

    def workflow_runner(*, workflow, **kwargs):
        assert workflow == "workflow.v2"
        return {
            "workflow": "workflow.v2",
            "specialists": {"artifact_refs": [], "artifact_hashes": {}, "payloads": {}},
            "conflicts": [{"dimension": "risk", "resolved": "veto"}],
            "proposal": {"proposal_id": "p-2", "action": "VETO"},
            "policy": {"policy_version": "policy.v1", "approved": False},
            "output": {"action": "VETO"},
        }

    result = _service(snapshot, bundle, workflow_runner=workflow_runner).replay(
        snapshot.decision_id, mode="WORKFLOW_REPLAY", overrides={"workflow": "workflow.v2"}
    )
    assert result["status"] == "SUCCEEDED"
    assert result["output"]["replayed"]["bundle"]["bundle_hash"] == bundle.bundle_hash
    assert set(result["changed_fields"]) >= {"workflow_version", "conflicts", "proposal_hash", "proposal", "output"}


def test_counterfactual_is_explicit_and_allowlisted():
    snapshot, bundle = _snapshot()
    def counterfactual_runner(*, market_regime, overrides, **kwargs):
        return {"output": {"action": "VETO", "market_regime": market_regime, "overrides": overrides}, "applied_overrides": dict(overrides)}

    result = _service(snapshot, bundle, counterfactual_runner=counterfactual_runner).replay(snapshot.decision_id, mode="COUNTERFACTUAL_REPLAY", overrides={"market_regime": "stress"})
    assert result["status"] == "SUCCEEDED"
    assert result["counterfactual"] is True
    assert result["changed_fields"] == ["market_regime", "output"]
    assert result["output"]["replayed"]["market_regime"] == "stress"
    missing_runner = _service(snapshot, bundle).replay(snapshot.decision_id, mode="COUNTERFACTUAL_REPLAY", overrides={"market_regime": "stress"})
    assert missing_runner["status"] == "REJECTED"
    assert missing_runner["error"] == "REPLAY_INPUT_INVALID"
    rejected = _service(snapshot, bundle).replay(snapshot.decision_id, mode="COUNTERFACTUAL_REPLAY", overrides={"model": "new"})
    assert rejected["status"] == "REJECTED"
    assert rejected["error"] == "INVALID_REPLAY_REQUEST"


def test_missing_snapshot_and_bundle_tampering_are_rejected():
    snapshot, bundle = _snapshot()
    missing = DecisionReplayService(snapshot_repository=_Snapshots(None), bundle_repository=_Bundles(bundle)).replay("decision-v2", mode="MODEL_REPLAY", overrides={"model": "new"})
    assert missing["status"] == "REJECTED"
    assert missing["error"] == "REPLAY_SNAPSHOT_REQUIRED"

    tampered = snapshot.model_dump(mode="python")
    tampered["input_bundle"]["payload"]["objective"] = "tampered"
    service = DecisionReplayService(snapshot_repository=_Snapshots(tampered), bundle_repository=_Bundles(bundle))
    rejected = service.replay(snapshot.decision_id, mode="EXACT_REPLAY")
    assert rejected["status"] == "REJECTED"
    assert rejected["error"] == "REPLAY_INPUT_INVALID"


def test_replay_request_is_immutable_and_validates_override_shape():
    request = ReplayRequest(decision_id="d-1", mode=ReplayMode.MODEL_REPLAY, overrides={"model": "m"})
    try:
        request.overrides["model"] = "other"
    except TypeError:
        pass
    else:
        raise AssertionError("replay overrides must be immutable")


def test_strict_replay_v2_never_falls_back_without_snapshot():
    class _NoSnapshot:
        def get_v3_for_decision(self, decision_id):
            return None

    service = DecisionReplayService(snapshot_repository=_NoSnapshot(), bundle_repository=_Bundles(None))
    request = ReplayRequest(decision_id="missing", mode=ReplayMode.EXACT_REPLAY)
    result = service.replay_v2(request)
    assert result["status"] == "REJECTED"
    assert result["error"] == "REPLAY_SNAPSHOT_REQUIRED"


def test_missing_runner_artifacts_are_rejected():
    snapshot, bundle = _snapshot()
    result = _service(snapshot, bundle, model_runner=lambda **kwargs: {}).replay(
        snapshot.decision_id, mode="MODEL_REPLAY", overrides={"model": "new"}
    )
    assert result["status"] == "REJECTED"
    assert result["error"] == "REPLAY_INPUT_INVALID"
    assert "proposal" in result["detail"]


def test_exact_runner_mismatch_is_audited_as_diff():
    snapshot, bundle = _snapshot()

    def exact_runner(**kwargs):
        return {
            "proposal": snapshot.proposal["payload"],
            "policy_evaluation": snapshot.policy,
            "output": {"final_decision": {"decision_id": snapshot.decision_id, "bundle_id": bundle.bundle_id, "action": "BUY"}},
        }

    result = _service(snapshot, bundle, exact_runner=exact_runner).replay(snapshot.decision_id, mode="EXACT_REPLAY")
    assert result["status"] == "REJECTED"
    assert result["error"] == "EXACT_REPLAY_MISMATCH"
    assert result["match"] is False
    assert any(item["field"] in {"output", "final"} for item in result["diffs"])


def test_model_replay_keeps_policy_version_but_recomputes_evaluation():
    snapshot, bundle = _snapshot()

    def model_runner(**kwargs):
        return {
            "proposal": {"action": "BUY"},
            "policy_evaluation": {"policy_version": "policy.v1", "checks": [{"rule": "new", "passed": False}]},
            "output": {"action": "VETO"},
        }

    result = _service(snapshot, bundle, model_runner=model_runner).replay(snapshot.decision_id, mode="MODEL_REPLAY", overrides={"model": "new"})
    assert result["status"] == "SUCCEEDED"
    assert "policy_version" in result["fixed_fields"]
    assert "policy_evaluation" in result["changed_fields"]


def test_component_runner_identity_mismatch_is_rejected_for_every_mode():
    snapshot, bundle = _snapshot()
    cases = (
        ("MODEL_REPLAY", {"model": "requested"}, "model_runner", {"model_identity": "other", "proposal": {"action": "HOLD"}, "policy_evaluation": {"policy_version": "policy.v1"}, "output": {"action": "HOLD"}}),
        ("SKILL_REPLAY", {"skill": "requested"}, "skill_runner", {"skill_identity": "other", "proposal": {"action": "HOLD"}, "policy_evaluation": {"policy_version": "policy.v1"}, "output": {"action": "HOLD"}}),
        ("POLICY_REPLAY", {"policy": {"policy_version": "requested"}}, "policy_runner", {"policy_evaluation": {"policy_version": "other"}, "output": {"action": "HOLD"}}),
        ("WORKFLOW_REPLAY", {"workflow": "requested"}, "workflow_runner", {"workflow_version": "other", "artifacts": {}, "conflicts": [], "proposal": {"action": "HOLD"}, "policy_evaluation": {"policy_version": "policy.v1"}, "output": {"action": "HOLD"}}),
    )
    for mode, override, runner_name, payload in cases:
        result = _service(snapshot, bundle, **{runner_name: lambda payload=payload, **kwargs: payload}).replay(
            snapshot.decision_id, mode=mode, overrides=override
        )
        assert result["status"] == "REJECTED", mode
        assert result["error"] == "REPLAY_INPUT_INVALID", mode
        assert "replacement" in result["detail"] or "version" in result["detail"] or "identity" in result["detail"], mode
