from __future__ import annotations
from engines.skill_evolution.candidate_workspace import CandidateWorkspace
from engines.skill_evolution.evaluator import run_contract_lint
from engines.skill_evolution.release_registry import SkillReleaseRegistry
from engines.skill_evolution.service import SkillEvolutionService

class SkillEvolutionRunner:
    def __init__(self, service: SkillEvolutionService | None = None, registry: SkillReleaseRegistry | None = None) -> None:
        self.service, self.registry = service or SkillEvolutionService(), registry or SkillReleaseRegistry()
    def evaluate_candidate(self, proposal_id: str) -> dict:
        proposal = self.service._get(proposal_id)
        workspace = CandidateWorkspace(proposal_id); workspace.create(proposal.skill_slug); workspace.apply(proposal.proposed_yaml_patch, proposal.proposed_markdown_patch)
        # The global linter validates active contracts; candidate schema is also parsed by YAML application above.
        lint = run_contract_lint(); self.service.static_validate(proposal_id, lint["passed"], lint["passed"])
        return {"proposal": proposal, "workspace": str(workspace.root), "lint": lint}
    def release(self, proposal_id: str, approved: bool = False) -> dict:
        proposal = self.service.promote(proposal_id, approved=approved)
        if proposal.status.value != "ACTIVE": return {"proposal": proposal, "released": False}
        target = self.registry.promote(proposal.skill_slug, proposal.base_version + 1, CandidateWorkspace(proposal_id).root)
        return {"proposal": proposal, "released": True, "path": str(target)}
