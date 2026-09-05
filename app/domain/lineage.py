"""Required cross-service identity for every new formal decision."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DecisionLineageV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    trace_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    decision_bundle_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    decision_snapshot_id: str = Field(min_length=1)
    market_snapshot_id: str = Field(min_length=1)
    factor_artifact_id: str = Field(min_length=1)
    content_snapshot_id: str = Field(min_length=1)
    portfolio_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    producer_commit: str = Field(min_length=1)
    execution_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def consistent_ids(self) -> DecisionLineageV1:
        if self.decision_id == self.decision_bundle_id:
            raise ValueError("LINEAGE_IDENTITIES_MUST_BE_DISTINCT")
        return self
