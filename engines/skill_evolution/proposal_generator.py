from __future__ import annotations
from engines.skill_evolution.models import SkillProposal

def proposal_from_review(skill_slug: str, base_version: int, review: dict) -> SkillProposal:
    failures = list(review.get("what_was_wrong") or review.get("root_causes") or [])
    return SkillProposal(skill_slug=skill_slug, base_version=base_version, hypothesis=f"Improve {skill_slug} based on structured decision review", observed_failures=[{"failure": item} for item in failures])
