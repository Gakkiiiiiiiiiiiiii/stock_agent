"""Guarded skill improvement workflow.

It produces proposals and validates candidate material, but never writes active
skill files itself.  Promotion remains an explicit, auditable state transition.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from financial_agent.config import load_yaml_config

from engines.skill_evolution.models import ProposalStatus, SkillProposal


def load_skill_evolution_config() -> dict:
    try:
        return dict(load_yaml_config("skill_evolution.yaml").get("skill_evolution") or {})
    except FileNotFoundError:
        return {"auto_promote": False, "max_token_regression_ratio": 1.10, "min_replay_improvement": 0.0}


class SkillEvolutionService:
    def __init__(self, config: dict | None = None) -> None:
        self.config = {**load_skill_evolution_config(), **(config or {})}
        self._proposals: dict[str, SkillProposal] = {}
        self._active_versions: dict[str, int] = {}
        self._previous_stable_versions: dict[str, int] = {}

    def propose(self, proposal: SkillProposal) -> SkillProposal:
        self._validate_scope(proposal)
        self._proposals[proposal.proposal_id] = proposal
        return proposal

    @staticmethod
    def _validate_scope(proposal: SkillProposal) -> None:
        forbidden = {"python", "migration", "broker", "risk_gate", "database"}
        keys = {str(key).lower() for key in proposal.proposed_yaml_patch}
        if keys & forbidden:
            raise ValueError("proposal modifies forbidden production scope")
        if proposal.proposed_markdown_patch and "```python" in proposal.proposed_markdown_patch.lower():
            raise ValueError("proposal must not introduce executable Python")

    def static_validate(self, proposal_id: str, linter_ok: bool, unit_tests_ok: bool) -> SkillProposal:
        proposal = self._get(proposal_id)
        result = {"stage": "STATIC", "linter_ok": linter_ok, "unit_tests_ok": unit_tests_ok}
        proposal.evaluations.append(result)
        proposal.status = ProposalStatus.STATIC_VALIDATED if linter_ok and unit_tests_ok else ProposalStatus.REJECTED
        return proposal

    def replay_validate(self, proposal_id: str, base: dict, candidate: dict) -> SkillProposal:
        proposal = self._get(proposal_id)
        if proposal.status != ProposalStatus.STATIC_VALIDATED:
            raise ValueError("proposal must pass static validation first")
        base_score, candidate_score = float(base.get("quality_score", 0)), float(candidate.get("quality_score", 0))
        base_tokens, candidate_tokens = float(base.get("tokens", 0)), float(candidate.get("tokens", 0))
        token_ok = not base_tokens or candidate_tokens <= base_tokens * float(self.config["max_token_regression_ratio"])
        replay_ok = candidate_score - base_score >= float(self.config["min_replay_improvement"])
        proposal.evaluations.append({"stage": "REPLAY", "base": base, "candidate": candidate, "replay_ok": replay_ok, "token_ok": token_ok})
        proposal.status = ProposalStatus.REPLAY_VALIDATED if replay_ok and token_ok else ProposalStatus.REJECTED
        return proposal

    def paper_validate(self, proposal_id: str, passed: bool, evidence: dict | None = None) -> SkillProposal:
        proposal = self._get(proposal_id)
        if proposal.status != ProposalStatus.REPLAY_VALIDATED:
            raise ValueError("proposal must pass replay validation first")
        proposal.evaluations.append({"stage": "PAPER", "passed": passed, "evidence": evidence or {}})
        proposal.status = ProposalStatus.PAPER_VALIDATED if passed else ProposalStatus.REJECTED
        return proposal

    def promote(self, proposal_id: str, approved: bool = False) -> SkillProposal:
        proposal = self._get(proposal_id)
        if proposal.status != ProposalStatus.PAPER_VALIDATED:
            raise ValueError("proposal must pass paper validation first")
        if not approved and not self.config.get("auto_promote", False):
            proposal.status = ProposalStatus.APPROVED
            return proposal
        self._previous_stable_versions[proposal.skill_slug] = self._active_versions.get(proposal.skill_slug, proposal.base_version)
        self._active_versions[proposal.skill_slug] = proposal.base_version + 1
        proposal.status = ProposalStatus.ACTIVE
        return proposal

    def rollback(self, skill_slug: str) -> int:
        if skill_slug not in self._previous_stable_versions:
            raise ValueError("no previous stable version")
        self._active_versions[skill_slug] = self._previous_stable_versions[skill_slug]
        for proposal in self._proposals.values():
            if proposal.skill_slug == skill_slug and proposal.status == ProposalStatus.ACTIVE:
                proposal.status = ProposalStatus.ROLLED_BACK
        return self._active_versions[skill_slug]

    def _get(self, proposal_id: str) -> SkillProposal:
        if proposal_id not in self._proposals:
            raise KeyError(proposal_id)
        return self._proposals[proposal_id]
