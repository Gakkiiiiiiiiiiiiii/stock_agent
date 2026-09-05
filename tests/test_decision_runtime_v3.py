from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.decision_runtime import DecisionRuntime
from app.model_gateway.metrics import MetricsRecorder
from contracts.decision_input import DecisionInputBundle, build_bundle
from contracts.decision_snapshot import (
    DecisionSnapshotV3,
    SnapshotBundleRef,
    canonical_hash,
)
from storage.repositories.decision_input_repository import DecisionInputBundleRepository
from storage.repositories.research_repository import (
    DecisionRepository,
    DecisionSnapshotRepository,
)

NOW = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)


class _Gateway:
    def __init__(self, order):
        self.order = order

    def collect(self, requests, *, decision_time):
        self.order.append("collect")
        return [], []


class _Bundles:
    def __init__(self, order):
        self.order = order

    def save(self, bundle):
        self.order.append("bundle")


class _Service:
    def __init__(self, order):
        self.order = order

    def save_decision(self, **payload):
        self.order.append("decision")
        assert payload.get("persist_snapshot") is False
        assert payload.get("schedule_evaluations") is False
        return {"decision_id": payload["id"]}


class _Snapshots:
    def __init__(self, order):
        self.order = order
        self.snapshot = None

    def save_v3(self, snapshot):
        self.order.append("snapshot")
        self.snapshot = snapshot
        return snapshot


def test_formal_runtime_persists_bundle_before_execution():
    order = []
    metrics = MetricsRecorder(events=[])
    runtime = DecisionRuntime(
        evidence_gateway=_Gateway(order), bundle_repository=_Bundles(order), decision_service=_Service(order), snapshot_repository=_Snapshots(order), clock=lambda: NOW, metrics=metrics,
    )
    result = runtime.decide(task_type="daily_market_decision", objective="test", subjects=["AAA"], as_of=NOW)
    assert order.index("bundle") < order.index("decision") < order.index("snapshot")
    assert result["bundle_id"] == result["decision"]["bundle_id"]
    assert result["decision"]["decision_action"] == "VETO"
    assert {event.name for event in metrics.snapshot()} >= {"bundle_build_latency_seconds", "specialist_latency_seconds", "decision_latency_seconds", "decision_quality_total", "risk_veto_total"}


def test_fallback_snapshot_contains_structured_six_specialist_artifacts():
    order = []
    snapshots = _Snapshots(order)
    runtime = DecisionRuntime(
        evidence_gateway=_Gateway(order), bundle_repository=_Bundles(order), decision_service=_Service(order), snapshot_repository=snapshots, clock=lambda: NOW,
    )
    runtime.decide(task_type="daily_market_decision", objective="test", subjects=["AAA"], as_of=NOW)
    assert len(snapshots.snapshot.specialists["artifact_refs"]) == 6
    assert set(snapshots.snapshot.specialists["artifact_refs"]) == set(snapshots.snapshot.specialists["artifact_hashes"])
    assert snapshots.snapshot.runtime["workflow_version"] == "decision-runtime.v3"


def test_snapshot_usage_preserves_actual_cost_and_derives_total_tokens():
    order = []
    snapshots = _Snapshots(order)
    runtime = DecisionRuntime(
        evidence_gateway=_Gateway(order), bundle_repository=_Bundles(order), decision_service=_Service(order), snapshot_repository=snapshots, clock=lambda: NOW,
        trusted_fallback=lambda **_: {"proposal": {"symbol": "AAA", "action": "HOLD", "confidence": 0.5}, "usage": {"input_tokens": 3, "output_tokens": 4, "cost": 1.25}},
    )
    runtime.decide(task_type="daily_market_decision", objective="usage", subjects=["AAA"], as_of=NOW)
    assert snapshots.snapshot.usage["input_tokens"] == 3
    assert snapshots.snapshot.usage["output_tokens"] == 4
    assert snapshots.snapshot.usage["total_tokens"] == 7
    assert snapshots.snapshot.usage["cost"] == 1.25
    assert snapshots.snapshot.usage["estimated_cost"] == 1.25


def test_formal_request_plan_collects_latest_market_snapshot_before_freeze():
    requests = DecisionRuntime._evidence_request_plan(
        "daily_market_decision", ["AAA"], {"skill": "daily-market-decision"}, NOW,
    )
    market = next(item for item in requests if item["evidence_type"] == "MARKET_SNAPSHOT")
    assert market["snapshot_id"] == "latest"
    assert market["params"] == {"snapshot_id": "latest"}


def _minimal_snapshot() -> DecisionSnapshotV3:
    bundle = build_bundle(created_at=NOW, decision_time=NOW, task_type="test", objective="test")
    bundle_payload = bundle.model_dump(mode="json")
    proposal_payload = {"proposal_id": "p-1", "action": "HOLD"}
    proposal_hash = canonical_hash(proposal_payload)
    policy_material = {"policy_result_id": "pe-1", "policy_version": "policy.v2", "checks": [{"rule": "r", "passed": True}], "approved": True}
    policy = {**policy_material, "result_hash": canonical_hash(policy_material)}
    final = {"action": "HOLD", "approved": True, "decision_id": "d-1", "bundle_id": bundle.bundle_id, "proposal_id": "p-1", "policy_result_id": "pe-1"}
    return DecisionSnapshotV3(
        decision_id="d-1", decision_time=NOW,
        runtime={"runtime_mode": "DETERMINISTIC_FALLBACK", "workflow_version": "decision-runtime.v3"},
        input_bundle=SnapshotBundleRef(bundle_id=bundle.bundle_id, bundle_hash=bundle.bundle_hash, schema_version=bundle.schema_version, payload=bundle_payload),
        dependencies={"quant": {"status": "OK"}, "factor": {"status": "OK"}, "content": {"status": "OK"}},
        evidence={"refs": [], "hashes": {}, "payloads": {}}, specialists={"artifact_refs": [], "artifact_hashes": {}, "payloads": {}},
        model={"provider": "test", "model": "test", "model_version": "1", "prompt_version": "1"}, skill={"slug": "test", "version": "1", "contract_hash": "c", "markdown_hash": "m"},
        proposal={"proposal_id": "p-1", "proposal_hash": proposal_hash, "payload": proposal_payload}, conflicts=[], risk={"risk_rule_version": "risk.v1", "veto": False}, policy=policy,
        output={"decision_id": "d-1", "final_decision_id": "d-1", "bundle_id": bundle.bundle_id, "proposal_id": "p-1", "policy_result_id": "pe-1", "final_decision": final, "final_decision_hash": canonical_hash(final)},
        lineage=[{"type": "BUNDLE", "id": bundle.bundle_id, "hash": bundle.bundle_hash}, {"type": "PROPOSAL", "id": "p-1", "hash": proposal_hash}, {"type": "POLICY", "id": "pe-1", "hash": policy["result_hash"]}, {"type": "FINAL_DECISION", "id": "d-1"}, {"type": "DECISION", "id": "d-1"}], decision_quality={"level": "MEDIUM"}, usage={"tool_calls": 0},
    )


def test_v3_rejects_tampered_hash_and_nested_mutation(isolated_database):
    snapshot = _minimal_snapshot()
    DecisionRepository().create(id="d-1", query="snapshot-test", candidates=[], decision_as_of=NOW)
    DecisionInputBundleRepository().save(DecisionInputBundle.model_validate(snapshot.input_bundle.payload))
    with pytest.raises(TypeError):
        snapshot.output["bundle_id"] = "tampered"
    with pytest.raises(ValueError):
        DecisionSnapshotRepository().save_v3(snapshot.model_copy(update={"output": {**snapshot.output, "bundle_id": "tampered"}}))
    saved = DecisionSnapshotRepository().save_v3(snapshot)
    assert DecisionSnapshotRepository().get_v3(saved.snapshot_id).snapshot_hash == snapshot.snapshot_hash
    assert DecisionSnapshotRepository().save_v3(snapshot).snapshot_id == saved.snapshot_id
