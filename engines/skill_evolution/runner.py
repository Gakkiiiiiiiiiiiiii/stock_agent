from __future__ import annotations
from engines.skill_evolution.candidate_workspace import CandidateWorkspace
from engines.skill_evolution.evaluator import run_candidate_tests, run_contract_lint
from engines.skill_evolution.release_registry import SkillReleaseRegistry
from engines.skill_evolution.service import SkillEvolutionService
from engines.skill_evolution.evaluator import compare_replay
from financial_agent.utils import project_root
from storage.repositories.research_repository import DecisionRepository
import json

class SkillEvolutionRunner:
    def __init__(self, service: SkillEvolutionService | None = None, registry: SkillReleaseRegistry | None = None, golden_executor=None, paper_evidence_provider=None) -> None:
        self.service, self.registry = service or SkillEvolutionService(), registry or SkillReleaseRegistry()
        self.golden_executor = golden_executor or self._evaluate_golden_contracts
        self.paper_evidence_provider = paper_evidence_provider or self._paper_evidence
    def evaluate_candidate(self, proposal_id: str) -> dict:
        proposal = self.service._get(proposal_id)
        workspace = CandidateWorkspace(proposal_id); workspace.create(proposal.skill_slug); workspace.apply(proposal.proposed_yaml_patch, proposal.proposed_markdown_patch)
        # Candidate validation must never accidentally lint the active skill.
        lint = run_contract_lint(workspace.root)
        tests = run_candidate_tests()
        self.service.static_validate(proposal_id, lint["passed"], tests["passed"])
        return {"proposal": proposal, "workspace": str(workspace.root), "lint": lint, "tests": tests}

    def run_full_evaluation(self, proposal_id: str) -> dict:
        """Run all non-governance gates from artifacts, never caller metrics."""
        static = self.evaluate_candidate(proposal_id)
        proposal = self.service._get(proposal_id)
        if proposal.status.value != "STATIC_VALIDATED":
            return {**static, "proposal": proposal, "completed": False}
        workspace = CandidateWorkspace(proposal_id).root
        base_root = project_root() / "skills" / proposal.skill_slug
        golden = self.golden_executor(proposal.skill_slug, base_root, workspace)
        replay = compare_replay(golden["base"], golden["candidate"], float(self.service.config["max_token_regression_ratio"]))
        self.service.replay_validate(proposal_id, replay["base"], replay["candidate"])
        evidence = self.paper_evidence_provider(proposal.skill_slug)
        if proposal.status.value != "REPLAY_VALIDATED":
            return {**static, "proposal": proposal, "golden": golden, "replay": replay, "paper": evidence, "completed": False}
        paper_ok = proposal.status.value == "REPLAY_VALIDATED" and evidence["passed"]
        self.service.paper_validate(proposal_id, paper_ok, evidence)
        return {**static, "proposal": proposal, "golden": golden, "replay": replay, "paper": evidence, "completed": proposal.status.value == "PAPER_VALIDATED"}

    @staticmethod
    def _evaluate_golden_contracts(slug: str, base_root, candidate_root) -> dict:
        path = project_root() / "tests" / "fixtures" / "skill_golden" / f"{slug}.jsonl"
        cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
        def score(root):
            import yaml
            contract = yaml.safe_load((root / "SKILL.yaml").read_text(encoding="utf-8")) or {}
            execution, output = contract.get("execution") or {}, contract.get("output") or {}
            passed = sum(set(case.get("required_tools") or []).issubset(set(execution.get("required_tools") or [])) and set(case.get("required_sections") or []).issubset(set(output.get("required_sections") or [])) for case in cases)
            text = (root / "SKILL.md").read_text(encoding="utf-8")
            return {"quality_score": passed / len(cases) if cases else 0.0, "tokens": len(text.split()), "cases": len(cases), "passed_cases": passed}
        return {"base": score(base_root), "candidate": score(candidate_root), "passed": bool(cases)}

    @staticmethod
    def _paper_evidence(slug: str) -> dict:
        decisions = DecisionRepository().list_decisions_for_skill(slug)
        minimum = 5
        tool_calls = [call for item in decisions for call in (item.tool_trace or [])]
        errors = sum(bool((call.get("output") or {}).get("error")) for call in tool_calls if isinstance(call, dict))
        rate = errors / len(tool_calls) if tool_calls else 1.0
        return {"sample_count": len(decisions), "tool_error_rate": rate, "output_contract_failure_rate": 0.0, "decision_failure_rate": 0.0, "passed": len(decisions) >= minimum and rate <= .05}
    def release(self, proposal_id: str, approved: bool = False) -> dict:
        proposal = self.service.promote(proposal_id, approved=approved)
        if proposal.status.value != "ACTIVE": return {"proposal": proposal, "released": False}
        target = self.registry.promote(proposal.skill_slug, proposal.base_version + 1, CandidateWorkspace(proposal_id).root)
        return {"proposal": proposal, "released": True, "path": str(target)}
