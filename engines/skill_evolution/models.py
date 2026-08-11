from __future__ import annotations

from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class ProposalStatus(StrEnum):
    PROPOSED = "PROPOSED"
    STATIC_VALIDATED = "STATIC_VALIDATED"
    REPLAY_VALIDATED = "REPLAY_VALIDATED"
    PAPER_VALIDATED = "PAPER_VALIDATED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


class SkillProposal(BaseModel):
    proposal_id: str = Field(default_factory=lambda: str(uuid4()))
    skill_slug: str
    base_version: int
    hypothesis: str
    observed_failures: list[dict] = Field(default_factory=list)
    proposed_yaml_patch: dict = Field(default_factory=dict)
    proposed_markdown_patch: str | None = None
    expected_improvement: dict = Field(default_factory=dict)
    status: ProposalStatus = ProposalStatus.PROPOSED
    evaluations: list[dict] = Field(default_factory=list)
