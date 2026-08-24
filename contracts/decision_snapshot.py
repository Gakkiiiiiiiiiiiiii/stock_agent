"""Immutable, replay-oriented ``decision.snapshot.v3`` contract."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contracts.immutable import ensure_json, freeze

SCHEMA_VERSION = "decision.snapshot.v3"


def canonical_hash(value: Any) -> str:
    checked = ensure_json(value)
    encoded = json.dumps(checked, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-blank")
    return value.strip()


class SnapshotBundleRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle_id: str
    bundle_hash: str
    schema_version: str = "decision-input.v1"
    payload: dict[str, Any]

    @field_validator("bundle_id", "bundle_hash", "schema_version")
    @classmethod
    def required(cls, value: str, info) -> str:
        return _required(value, info.field_name)

    @model_validator(mode="after")
    def validate_payload(self) -> "SnapshotBundleRef":
        payload = ensure_json(self.payload)
        supplied_hash = payload.get("bundle_hash") if isinstance(payload, dict) else None
        material = dict(payload)
        material.pop("bundle_hash", None)
        if supplied_hash is not None and supplied_hash != self.bundle_hash:
            raise ValueError("bundle payload hash does not match bundle_hash")
        if canonical_hash(material) != self.bundle_hash:
            raise ValueError("bundle_hash does not match canonical bundle payload")
        if payload.get("bundle_id") != self.bundle_id:
            raise ValueError("bundle payload bundle_id does not match reference")
        if payload.get("schema_version") != self.schema_version:
            raise ValueError("bundle payload schema_version does not match reference")
        object.__setattr__(self, "payload", freeze(payload))
        return self


class DecisionSnapshotV3(BaseModel):
    """Canonical audit object for every formal decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: str(uuid4()))
    decision_id: str
    decision_time: datetime
    schema_version: Literal["decision.snapshot.v3"] = SCHEMA_VERSION
    runtime: dict[str, Any]
    input_bundle: SnapshotBundleRef
    dependencies: dict[str, Any]
    evidence: dict[str, Any]
    specialists: dict[str, Any]
    model: dict[str, Any]
    skill: dict[str, Any]
    proposal: dict[str, Any]
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    risk: dict[str, Any]
    policy: dict[str, Any]
    output: dict[str, Any]
    lineage: list[dict[str, Any]]
    decision_quality: dict[str, Any]
    usage: dict[str, Any]
    snapshot_hash: str = ""

    @field_validator("snapshot_id", "decision_id")
    @classmethod
    def ids_nonblank(cls, value: str, info) -> str:
        return _required(value, info.field_name)

    @field_validator("decision_time")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision_time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_integrity(self) -> "DecisionSnapshotV3":
        for name in ("runtime", "dependencies", "evidence", "specialists", "model", "skill", "proposal", "risk", "policy", "output", "decision_quality", "usage"):
            ensure_json(getattr(self, name), name)
        ensure_json(self.conflicts, "conflicts")
        ensure_json(self.lineage, "lineage")
        for key in ("runtime_mode", "workflow_version"):
            _required(str(self.runtime.get(key, "")), f"runtime.{key}")
        for system in ("quant", "factor", "content"):
            if system not in self.dependencies:
                raise ValueError(f"dependencies.{system} is required")
        for key in ("provider", "model", "model_version", "prompt_version"):
            _required(str(self.model.get(key, "")), f"model.{key}")
        for key in ("slug", "version", "contract_hash", "markdown_hash"):
            _required(str(self.skill.get(key, "")), f"skill.{key}")
        self._validate_evidence_segment()
        self._validate_specialist_segment()
        proposal_id = _required(str(self.proposal.get("proposal_id", "")), "proposal.proposal_id")
        proposal_hash = _required(str(self.proposal.get("proposal_hash", "")), "proposal.proposal_hash")
        proposal_payload = self.proposal.get("payload")
        ensure_json(proposal_payload, "proposal.payload")
        if proposal_hash != canonical_hash(proposal_payload):
            raise ValueError("proposal_hash does not match proposal payload")
        _required(str(self.risk.get("risk_rule_version", "")), "risk.risk_rule_version")
        policy_id = _required(str(self.policy.get("policy_result_id", "")), "policy.policy_result_id")
        _required(str(self.policy.get("policy_version", "")), "policy.policy_version")
        checks = self.policy.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ValueError("policy checks must be a non-empty ordered list")
        policy_material = {key: value for key, value in self.policy.items() if key != "result_hash"}
        if _required(str(self.policy.get("result_hash", "")), "policy.result_hash") != canonical_hash(policy_material):
            raise ValueError("policy result_hash does not match policy checks/result")
        output = self.output
        if output.get("bundle_id") != self.input_bundle.bundle_id:
            raise ValueError("final decision bundle_id does not match input bundle")
        if output.get("decision_id") != self.decision_id:
            raise ValueError("final decision decision_id does not match snapshot decision_id")
        if output.get("proposal_id") != proposal_id or output.get("policy_result_id") != policy_id:
            raise ValueError("final decision references do not match proposal/policy")
        _required(str(output.get("final_decision_id", "")), "output.final_decision_id")
        final = output.get("final_decision")
        ensure_json(final, "output.final_decision")
        if not isinstance(final, dict) or final.get("decision_id") != self.decision_id or final.get("bundle_id") != self.input_bundle.bundle_id:
            raise ValueError("final decision lineage is inconsistent")
        if _required(str(output.get("final_decision_hash", "")), "output.final_decision_hash") != canonical_hash(final):
            raise ValueError("final_decision_hash does not match final decision")
        if not self.lineage:
            raise ValueError("lineage cannot be empty")
        lineage_types = {str(item.get("type")): item for item in self.lineage}
        for required_type in ("BUNDLE", "PROPOSAL", "POLICY", "FINAL_DECISION", "DECISION"):
            if required_type not in lineage_types:
                raise ValueError(f"lineage.{required_type} is required")
        if lineage_types["BUNDLE"].get("id") != self.input_bundle.bundle_id or lineage_types["BUNDLE"].get("hash") not in {None, self.input_bundle.bundle_hash}:
            raise ValueError("bundle lineage does not match input bundle")
        if lineage_types["PROPOSAL"].get("id") != proposal_id or lineage_types["PROPOSAL"].get("hash") not in {None, proposal_hash}:
            raise ValueError("proposal lineage does not match proposal")
        if lineage_types["POLICY"].get("id") != policy_id or lineage_types["POLICY"].get("hash") not in {None, self.policy.get("result_hash")}:
            raise ValueError("policy lineage does not match policy")
        if lineage_types["FINAL_DECISION"].get("id") != output.get("final_decision_id"):
            raise ValueError("final decision lineage does not match output")
        if lineage_types["DECISION"].get("id") != self.decision_id:
            raise ValueError("decision lineage does not match snapshot")
        for item in self.lineage:
            if not isinstance(item, dict) or not item.get("type") or not item.get("id"):
                raise ValueError("lineage entries require type and id")
        if not self.decision_quality.get("level"):
            raise ValueError("decision_quality.level is required")
        if "tool_calls" not in self.usage:
            raise ValueError("usage.tool_calls is required")
        digest = snapshot_hash(self)
        if self.snapshot_hash and self.snapshot_hash != digest:
            raise ValueError("snapshot_hash does not match canonical snapshot contents")
        object.__setattr__(self, "snapshot_hash", digest)
        for name in ("runtime", "dependencies", "evidence", "specialists", "model", "skill", "proposal", "conflicts", "risk", "policy", "output", "lineage", "decision_quality", "usage"):
            object.__setattr__(self, name, freeze(getattr(self, name)))
        return self

    def _validate_evidence_segment(self) -> None:
        refs = self.evidence.get("refs")
        hashes = self.evidence.get("hashes")
        if not isinstance(refs, list) or not isinstance(hashes, dict):
            raise ValueError("evidence refs and hashes are required")
        payloads = self.evidence.get("payloads") or {}
        for ref in refs:
            ref_id = ref.get("id") if isinstance(ref, dict) else ref
            _required(str(ref_id or ""), "evidence.ref.id")
            digest = _required(str(hashes.get(ref_id, "")), f"evidence.hashes.{ref_id}")
            if ref_id not in payloads:
                raise ValueError(f"evidence payload missing: {ref_id}")
            if digest != canonical_hash(payloads[ref_id]):
                raise ValueError(f"evidence hash mismatch: {ref_id}")

    def _validate_specialist_segment(self) -> None:
        refs = self.specialists.get("artifact_refs")
        hashes = self.specialists.get("artifact_hashes")
        if not isinstance(refs, list) or not isinstance(hashes, dict):
            raise ValueError("specialist artifact refs and hashes are required")
        payloads = self.specialists.get("payloads") or {}
        for ref in refs:
            ref_id = ref.get("id") if isinstance(ref, dict) else ref
            _required(str(ref_id or ""), "specialist.artifact_ref.id")
            digest = _required(str(hashes.get(ref_id, "")), f"specialists.artifact_hashes.{ref_id}")
            if ref_id not in payloads:
                raise ValueError(f"specialist artifact payload missing: {ref_id}")
            if digest != canonical_hash(payloads[ref_id]):
                raise ValueError(f"specialist artifact hash mismatch: {ref_id}")


def snapshot_hash(snapshot: DecisionSnapshotV3) -> str:
    return canonical_hash(snapshot.model_dump(mode="json", exclude={"snapshot_hash"}))


DecisionSnapshot = DecisionSnapshotV3

__all__ = ["SCHEMA_VERSION", "DecisionSnapshot", "DecisionSnapshotV3", "SnapshotBundleRef", "canonical_hash", "snapshot_hash"]
