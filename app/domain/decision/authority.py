"""Explicit authority markers used on every public decision-facing response."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DecisionAuthority(StrEnum):
    FORMAL = "FORMAL"
    ANALYSIS_ONLY = "ANALYSIS_ONLY"
    COMPATIBILITY_READ_ONLY = "COMPATIBILITY_READ_ONLY"


class FormalDecisionResponseV2(BaseModel):
    """Stable public representation of a finalized formal decision.

    ``execution_eligible`` is intentionally derived/validated at the boundary;
    callers cannot mark an analysis result as executable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    contract: str = "formal-decision.v2"
    authority: DecisionAuthority = DecisionAuthority.FORMAL
    decision_id: str = Field(min_length=1)
    decision_snapshot_id: str = Field(min_length=1)
    decision_bundle_id: str = Field(min_length=1)
    portfolio_id: str = Field(min_length=1)
    status: Literal["HOLD", "APPROVED", "REJECTED"]
    execution_eligible: bool = False
    valid_until: datetime
    lineage: dict[str, Any]

    @model_validator(mode="after")
    def validate_formal(self) -> FormalDecisionResponseV2:
        if self.contract != "formal-decision.v2":
            raise ValueError("FORMAL_CONTRACT_REQUIRED")
        if self.authority is not DecisionAuthority.FORMAL:
            raise ValueError("FORMAL_AUTHORITY_REQUIRED")
        required = {"market_snapshot_id", "factor_artifact_id", "content_snapshot_id", "policy_version", "producer_commit"}
        if not required.issubset(self.lineage):
            raise ValueError("LINEAGE_REQUIRED")
        return self


def analysis_response(payload: dict[str, Any], *, compatibility: bool = False) -> dict[str, Any]:
    """Add a non-authoritative marker without leaking execution semantics."""
    result = dict(payload)
    result.pop("execution_eligible", None)
    # Analysis adapters return narrative payloads.  Authorization is meaningful
    # only on the formal v2 write path, so do not let an upstream field make an
    # analysis response a carrier for execution authority.
    result.pop("authorization_envelope", None)
    result.pop("execution_authorization", None)
    result.pop("allowed_actions", None)
    result.pop("max_notional", None)
    result["authority"] = (DecisionAuthority.COMPATIBILITY_READ_ONLY if compatibility else DecisionAuthority.ANALYSIS_ONLY).value
    result["execution_eligible"] = False
    return result
