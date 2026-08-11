from __future__ import annotations

from engines.skill_evolution import SkillEvolutionRunner, SkillEvolutionService, SkillProposal
from engines.skill_evolution.golden_executor import SkillGoldenExecutor
from engines.skill_evolution.models import ProposalStatus
from financial_agent.utils import project_root


def _proposal(service: SkillEvolutionService):
    return service.propose(SkillProposal(skill_slug="daily-market-decision", base_version=2, hypothesis="golden gate"))


def _golden(base_score=1, candidate_score=1, base_tokens=10, candidate_tokens=10, passed=True):
    return {"base": {"quality_score": base_score, "tokens": base_tokens}, "candidate": {"quality_score": candidate_score, "tokens": candidate_tokens}, "passed": passed}


def test_default_golden_dataset_executes_active_contract():
    root = project_root() / "skills" / "daily-market-decision"
    result = SkillEvolutionRunner._evaluate_golden_contracts("daily-market-decision", root, root)
    assert result["passed"] is True
    assert result["candidate"]["passed_cases"] == 5
    assert result["candidate"]["executions"][0]["tool_calls"]


def test_missing_or_failed_golden_is_hard_rejection():
    root = project_root() / "skills" / "daily-market-decision"
    missing = SkillEvolutionRunner._evaluate_golden_contracts("no-golden-dataset", root, root)
    assert missing["passed"] is False
    failed_case = SkillGoldenExecutor().evaluate("daily-market-decision", root, root, [{"id": "missing-tool", "query": "q", "required_tools": ["not_declared"], "structured_output": {}}])
    assert failed_case["passed"] is False

    service = SkillEvolutionService()
    proposal = _proposal(service)
    result = SkillEvolutionRunner(service=service, golden_executor=lambda *_: _golden(passed=False)).run_full_evaluation(proposal.proposal_id)
    assert result["reject_reason"] == "GOLDEN_CASES_FAILED"
    assert proposal.status == ProposalStatus.REJECTED


def test_candidate_regression_and_token_regression_reject_before_paper():
    for golden in (_golden(base_score=1, candidate_score=.9), _golden(base_tokens=10, candidate_tokens=12)):
        service = SkillEvolutionService(config={"max_token_regression_ratio": 1.1})
        proposal = _proposal(service)
        result = SkillEvolutionRunner(service=service, golden_executor=lambda *_args, value=golden: value, paper_evidence_provider=lambda _: {"passed": True}).run_full_evaluation(proposal.proposal_id)
        assert result["completed"] is False
        assert proposal.status == ProposalStatus.REJECTED


def test_paper_sample_shortage_rejects_and_full_evidence_validates():
    service = SkillEvolutionService()
    proposal = _proposal(service)
    insufficient = SkillEvolutionRunner(service=service, golden_executor=lambda *_: _golden(), paper_evidence_provider=lambda _: {"sample_count": 0, "passed": False})
    assert insufficient.run_full_evaluation(proposal.proposal_id)["completed"] is False
    assert proposal.status == ProposalStatus.REJECTED

    service = SkillEvolutionService()
    proposal = _proposal(service)
    runner = SkillEvolutionRunner(service=service, golden_executor=lambda *_: _golden(), paper_evidence_provider=lambda _: {"sample_count": 5, "evaluated_sample_count": 5, "tool_error_rate": 0, "output_contract_failure_rate": 0, "decision_failure_rate": 0, "passed": True})
    assert runner.run_full_evaluation(proposal.proposal_id)["completed"] is True
    assert proposal.status == ProposalStatus.PAPER_VALIDATED
