"""Typed contracts for Decision Replay v2.

Replay v2 is deliberately a small, audit-oriented contract.  A replay is
anchored to a persisted ``DecisionSnapshotV3`` and may replace only the
component selected by its mode.  The executable runner is injected by the
engine; this module contains no I/O or data-provider integration.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contracts.immutable import ensure_json, freeze


class ReplayMode(StrEnum):
    """Canonical v2 modes plus names retained for the v1 adapter."""

    EXACT_REPLAY = "EXACT_REPLAY"
    MODEL_REPLAY = "MODEL_REPLAY"
    SKILL_REPLAY = "SKILL_REPLAY"
    POLICY_REPLAY = "POLICY_REPLAY"
    WORKFLOW_REPLAY = "WORKFLOW_REPLAY"
    COUNTERFACTUAL_REPLAY = "COUNTERFACTUAL_REPLAY"

    # v1 compatibility aliases are intentionally not aliases of a v2 mode:
    # they select the old deterministic adapter and cannot silently acquire
    # v2 semantics when old records do not have a v3 snapshot.
    ORIGINAL = "original"
    CURRENT = "current"
    MULTI_AGENT = "multi_agent"


V2_REPLAY_MODES: tuple[ReplayMode, ...] = (
    ReplayMode.EXACT_REPLAY,
    ReplayMode.MODEL_REPLAY,
    ReplayMode.SKILL_REPLAY,
    ReplayMode.POLICY_REPLAY,
    ReplayMode.WORKFLOW_REPLAY,
    ReplayMode.COUNTERFACTUAL_REPLAY,
)
LEGACY_REPLAY_MODES: tuple[ReplayMode, ...] = (
    ReplayMode.ORIGINAL,
    ReplayMode.CURRENT,
    ReplayMode.MULTI_AGENT,
)

# Names are intentionally singular and explicit.  ``risk`` or ``state`` are
# too broad to be auditable and could accidentally permit a model/workflow
# replacement through the counterfactual path.
COUNTERFACTUAL_OVERRIDE_KEYS = frozenset(
    {"market_regime", "risk_parameter", "portfolio_state", "strategy", "policy"}
)


class ReplayRequest(BaseModel):
    """Immutable request accepted by the replay engine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(min_length=1)
    mode: ReplayMode
    overrides: dict[str, Any] = Field(default_factory=dict)
    # Optional explicit anchor prevents callers from selecting another
    # snapshot than the one attached to the decision by accident.
    snapshot_id: str | None = None

    @field_validator("decision_id", "snapshot_id")
    @classmethod
    def non_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("identifier must not be blank")
        return value

    @field_validator("overrides")
    @classmethod
    def json_overrides(cls, value: dict[str, Any]) -> dict[str, Any]:
        ensure_json(value, "overrides")
        return freeze(value)

    @model_validator(mode="after")
    def validate_mode_overrides(self) -> "ReplayRequest":
        keys = set(self.overrides)
        mode = self.mode
        required = {
            ReplayMode.MODEL_REPLAY: "model",
            ReplayMode.SKILL_REPLAY: "skill",
            ReplayMode.POLICY_REPLAY: "policy",
            ReplayMode.WORKFLOW_REPLAY: "workflow",
        }
        if mode in required:
            expected = required[mode]
            if keys != {expected}:
                raise ValueError(f"{mode.value} requires exactly the {expected!r} override")
        elif mode is ReplayMode.COUNTERFACTUAL_REPLAY:
            if not keys:
                raise ValueError("COUNTERFACTUAL_REPLAY requires at least one override")
            unknown = keys - COUNTERFACTUAL_OVERRIDE_KEYS
            if unknown:
                raise ValueError(f"unsupported counterfactual override(s): {sorted(unknown)}")
        elif mode in V2_REPLAY_MODES and keys:
            raise ValueError(f"{mode.value} does not accept overrides")
        return self


class ReplayDiff(BaseModel):
    """One auditable fixed/changed field comparison."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str = Field(min_length=1)
    baseline: Any = None
    replayed: Any = None
    changed: bool

    @model_validator(mode="after")
    def json_values(self) -> "ReplayDiff":
        ensure_json(self.baseline, "diff.baseline")
        ensure_json(self.replayed, "diff.replayed")
        if self.changed != (self.baseline != self.replayed):
            raise ValueError("diff.changed must match baseline/replayed values")
        return self


class ReplayResult(BaseModel):
    """Serializable result shared by API, audit and evaluation callers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(min_length=1)
    snapshot_id: str | None = None
    mode: ReplayMode
    status: Literal["SUCCEEDED", "REJECTED"]
    match: bool = True
    verification: Literal["FULL_REPLAY", "INTEGRITY_ONLY"] = "FULL_REPLAY"
    counterfactual: bool = False
    bundle_id: str | None = None
    bundle_hash: str | None = None
    fixed_fields: tuple[str, ...] = ()
    changed_fields: tuple[str, ...] = ()
    diffs: tuple[ReplayDiff, ...] = ()
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    detail: str | None = None

    @field_validator("output")
    @classmethod
    def json_output(cls, value: dict[str, Any]) -> dict[str, Any]:
        ensure_json(value, "result.output")
        return freeze(value)

    @model_validator(mode="after")
    def status_contract(self) -> "ReplayResult":
        if self.status == "REJECTED" and not self.error:
            raise ValueError("rejected replay requires an error")
        if self.counterfactual and self.mode is not ReplayMode.COUNTERFACTUAL_REPLAY:
            raise ValueError("counterfactual marker requires COUNTERFACTUAL_REPLAY")
        if self.mode is ReplayMode.COUNTERFACTUAL_REPLAY and self.status == "SUCCEEDED" and not self.counterfactual:
            raise ValueError("COUNTERFACTUAL_REPLAY must be explicitly marked counterfactual")
        if set(self.fixed_fields) & set(self.changed_fields):
            raise ValueError("fixed and changed replay fields must be disjoint")
        if not set(item.field for item in self.diffs).issubset(set(self.changed_fields)):
            raise ValueError("replay diff fields must be declared as changed")
        return self


__all__ = [
    "COUNTERFACTUAL_OVERRIDE_KEYS",
    "LEGACY_REPLAY_MODES",
    "ReplayDiff",
    "ReplayMode",
    "ReplayRequest",
    "ReplayResult",
    "V2_REPLAY_MODES",
]
