"""Focused regression tests for proposal/policy/final-decision v2 contracts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from contracts.decision import FinalInvestmentDecision, PolicyCheckResult, PolicyEvaluation
from contracts.proposal import DecisionHorizon, InvestmentProposalV2, ModelIdentity, NarrativeReport, ThesisPoint
from engines.policy import PolicyContext, PolicyEngine, PolicyLimits


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def proposal(**overrides) -> InvestmentProposalV2:
    values = {
        "proposal_id": "p-1",
        "subject_type": "SYMBOL",
        "subject_key": "AAA",
        "action": "BUY",
        "target_weight": 0.10,
        "weight_delta": None,
        "confidence": 0.8,
        "horizon": DecisionHorizon(period="swing"),
        "thesis": [ThesisPoint(statement="support", evidence_refs=["ev-1"])],
        "catalysts": ["catalyst"],
        "entry_conditions": [],
        "invalidation_conditions": ["risk changes"],
        "expected_risks": ["market"],
        "evidence_refs": ["ev-1"],
        "specialist_artifact_refs": [],
        "unknowns": [],
        "generated_by": ModelIdentity(provider="test", model="model-1"),
    }
    values.update(overrides)
    return InvestmentProposalV2.build(**values)


def healthy_context(**overrides) -> PolicyContext:
    values = {
        "domain_coverage": {"market": 1.0, "factor": 1.0, "risk": 1.0},
        "dependency_health": {"quant": "OK", "portfolio": "OK"},
        "evidence_freshness_seconds": 10,
        "portfolio_available": True,
        "portfolio_snapshot_fresh": True,
        "gross_exposure": 0.20,
        "net_exposure": 0.10,
        "liquidity_ok": {"AAA": True},
        "security_is_st": {"AAA": False},
        "security_is_suspended": {"AAA": False},
    }
    values.update(overrides)
    return PolicyContext(**values)


def test_proposal_hash_stable_and_nested_values_are_immutable():
    first = proposal()
    second = proposal()
    assert first.proposal_hash == second.proposal_hash
    with pytest.raises(TypeError):
        first.thesis.append(ThesisPoint(statement="new"))
    with pytest.raises(TypeError):
        first.catalysts += ["new"]
    with pytest.raises(ValidationError):
        first.model_copy(update={"target_weight": 0.2})


def test_proposal_rejects_forged_hash_and_report_is_not_a_proposal():
    values = proposal().model_dump(mode="python")
    values["proposal_hash"] = "0" * 64
    with pytest.raises(ValidationError):
        InvestmentProposalV2.model_validate(values)
    report = NarrativeReport(summary="human-readable explanation")
    with pytest.raises(ValidationError):
        InvestmentProposalV2.model_validate(report.model_dump())


@pytest.mark.parametrize("action, target, delta", [("HOLD", 0.1, None), ("BUY", None, None), ("BUY", 0.1, -0.1), ("REDUCE", None, 0.1)])
def test_proposal_action_and_weight_semantics(action, target, delta):
    with pytest.raises(ValidationError):
        proposal(action=action, target_weight=target, weight_delta=delta)


def test_policy_v2_is_ordered_and_hashes_adjustment_trace():
    evaluation = PolicyEngine().evaluate(proposal(target_weight=0.20), healthy_context())
    assert isinstance(evaluation, PolicyEvaluation)
    assert evaluation.approved is True
    assert [check.rule_id for check in evaluation.checks] == [
        "SINGLE_POSITION_LIMIT", "PORTFOLIO_GROSS_LIMIT", "PORTFOLIO_NET_LIMIT",
        "INDUSTRY_EXPOSURE", "THEME_EXPOSURE", "LIQUIDITY", "DRAWDOWN_MODE",
        "RESTRICTED_UNIVERSE", "ST_SUSPENSION", "MINIMUM_EVIDENCE", "MINIMUM_CONFIDENCE",
        "FACTOR_COVERAGE", "DEPENDENCY_HEALTH", "EVIDENCE_FRESHNESS",
        "PORTFOLIO_SNAPSHOT_FRESHNESS", "RISK_VETO",
    ]
    assert evaluation.adjusted_value == 0.1
    assert evaluation.result_hash
    with pytest.raises(ValidationError):
        evaluation.model_copy(update={"adjusted_value": 0.2})


@pytest.mark.parametrize(
    "context, rule",
    [
        (PolicyContext(domain_coverage={"market": 1, "factor": 1, "risk": 1}, dependency_health={"quant": "UNAVAILABLE", "portfolio": "OK"}, evidence_freshness_seconds=1, portfolio_available=True, portfolio_snapshot_fresh=True), "DEPENDENCY_HEALTH"),
        (PolicyContext(domain_coverage={"market": 1, "factor": 1, "risk": 1}, dependency_health={"quant": "OK", "portfolio": "OK"}, evidence_freshness_seconds=90000, portfolio_available=True, portfolio_snapshot_fresh=True), "EVIDENCE_FRESHNESS"),
        (PolicyContext(domain_coverage={"market": 1, "factor": 1, "risk": 1}, dependency_health={"quant": "OK", "portfolio": "OK"}, evidence_freshness_seconds=1, portfolio_available=False, portfolio_snapshot_fresh=False), "PORTFOLIO_SNAPSHOT_FRESHNESS"),
        (PolicyContext(domain_coverage={"market": 1, "factor": 1, "risk": 1}, dependency_health={"quant": "OK", "portfolio": "OK"}, evidence_freshness_seconds=1, portfolio_available=True, portfolio_snapshot_fresh=True, risk_veto=True), "RISK_VETO"),
    ],
)
def test_policy_v2_hard_gates(context, rule):
    evaluation = PolicyEngine().evaluate(proposal(), context)
    assert evaluation.approved is False
    failed = {check.rule_id for check in evaluation.checks if not check.passed}
    assert rule in failed


@pytest.mark.parametrize(
    "limits, context, expected_rule",
    [
        (PolicyLimits(portfolio_gross_limit=0.25), healthy_context(gross_exposure=0.22), "PORTFOLIO_GROSS_LIMIT"),
        (PolicyLimits(portfolio_net_limit=0.15), healthy_context(net_exposure=0.14), "PORTFOLIO_NET_LIMIT"),
        (PolicyLimits(industry_exposure_limit=0.25), healthy_context(subject_sectors={"AAA": "tech"}, industry_weights={"tech": 0.20}), "INDUSTRY_EXPOSURE"),
        (PolicyLimits(theme_exposure_limit=0.15), healthy_context(subject_themes={"AAA": "ai"}, theme_weights={"ai": 0.10}), "THEME_EXPOSURE"),
        (PolicyLimits(), healthy_context(liquidity_ok={"AAA": False}), "LIQUIDITY"),
        (PolicyLimits(), healthy_context(portfolio_drawdown_mode=True), "DRAWDOWN_MODE"),
        (PolicyLimits(), healthy_context(restricted_universe=["AAA"]), "RESTRICTED_UNIVERSE"),
        (PolicyLimits(), healthy_context(security_is_st={"AAA": True}), "ST_SUSPENSION"),
        (PolicyLimits(), healthy_context(domain_coverage={"market": 1, "factor": 0.1, "risk": 1}), "MINIMUM_EVIDENCE"),
        (PolicyLimits(min_confidence=0.9), healthy_context(), "MINIMUM_CONFIDENCE"),
        (PolicyLimits(min_factor_coverage=0.9), healthy_context(domain_coverage={"market": 1, "factor": 0.1, "risk": 1}), "FACTOR_COVERAGE"),
    ],
)
def test_each_policy_family_has_deterministic_behavior(limits, context, expected_rule):
    candidate = proposal(target_weight=0.08, confidence=0.8)
    if expected_rule == "MINIMUM_CONFIDENCE":
        candidate = proposal(target_weight=0.08, confidence=0.5)
    evaluation = PolicyEngine(limits=limits).evaluate(candidate, context)
    check = next(item for item in evaluation.checks if item.rule_id == expected_rule)
    assert not check.passed or check.severity == "ADJUST"


def test_optional_factor_content_degradation_is_nonblocking_but_required_is_blocking():
    optional = healthy_context(dependency_health={"quant": "OK", "factor": "UNAVAILABLE", "content": "DEGRADED"})
    assert PolicyEngine().evaluate(proposal(), optional).approved is True
    required = healthy_context(
        dependency_health={"quant": "OK", "factor": "UNAVAILABLE"},
        required_dependencies=["factor"],
    )
    evaluation = PolicyEngine().evaluate(proposal(), required)
    assert evaluation.approved is False
    assert not next(item for item in evaluation.checks if item.rule_id == "DEPENDENCY_HEALTH").passed


def test_factor_coverage_follows_active_skill_requirements():
    optional = healthy_context(
        domain_coverage={"market": 1.0, "risk": 1.0},
        required_domains=["market", "risk"],
        dependency_health={"quant": "OK", "factor": "UNAVAILABLE"},
    )
    optional_eval = PolicyEngine().evaluate(proposal(), optional)
    assert optional_eval.approved is True
    factor_check = next(item for item in optional_eval.checks if item.rule_id == "FACTOR_COVERAGE")
    assert factor_check.passed is True and factor_check.severity == "WARN"

    required = healthy_context(
        domain_coverage={"market": 1.0, "risk": 1.0},
        required_domains=["market", "factor", "risk"],
        dependency_health={"quant": "OK", "factor": "UNAVAILABLE"},
    )
    required_eval = PolicyEngine().evaluate(proposal(), required)
    assert required_eval.approved is False
    assert not next(item for item in required_eval.checks if item.rule_id == "FACTOR_COVERAGE").passed


def test_missing_symbol_security_facts_are_not_assumed_safe():
    missing = healthy_context(liquidity_ok={}, security_is_st={}, security_is_suspended={})
    evaluation = PolicyEngine().evaluate(proposal(), missing)
    assert evaluation.approved is False
    assert {item.rule_id for item in evaluation.checks if not item.passed} >= {"LIQUIDITY", "ST_SUSPENSION"}
    reduction = PolicyEngine().evaluate(proposal(action="REDUCE", target_weight=0.0, weight_delta=None), missing)
    assert reduction.approved is True


def test_adjustments_carry_forward_between_gross_and_net_rules():
    context = healthy_context(gross_exposure=0.96, net_exposure=0.96)
    evaluation = PolicyEngine().evaluate(proposal(target_weight=0.10), context)
    gross = next(item for item in evaluation.checks if item.rule_id == "PORTFOLIO_GROSS_LIMIT")
    net = next(item for item in evaluation.checks if item.rule_id == "PORTFOLIO_NET_LIMIT")
    assert gross.adjusted_value == pytest.approx(0.04)
    assert net.original_value == pytest.approx(gross.adjusted_value)
    assert evaluation.adjusted_value == pytest.approx(net.adjusted_value)


def test_reduction_to_zero_remains_valid_but_veto_has_no_weight():
    reduction = proposal(action="REDUCE", target_weight=0.0, weight_delta=None)
    evaluation = PolicyEngine().evaluate(reduction, healthy_context())
    assert evaluation.approved is True
    final = FinalInvestmentDecision.build(
        decision_id="d-1", decision_time=NOW, valid_from=NOW,
        subject_type="SYMBOL", subject_key="AAA", decision_action="VETO",
        investment_action="REDUCE", target_weight=None, weight_delta=None,
        confidence=0.8, decision_quality="HIGH", rationale=["risk veto"],
        evidence_refs=["ev-1"], proposal_id="p-1", policy_result_id=evaluation.policy_result_id,
        invalidation_conditions=[], bundle_id="bundle-1",
    )
    assert final.decision_action == "VETO"
    assert final.target_weight is None


def test_final_decision_time_and_actionable_fields_are_validated():
    with pytest.raises(ValidationError):
        FinalInvestmentDecision.build(
            decision_id="d-1", decision_time=NOW, valid_from=NOW - timedelta(seconds=1),
            subject_type="SYMBOL", subject_key="AAA", decision_action="APPROVE",
            investment_action="BUY", target_weight=0.1, weight_delta=None, confidence=0.8,
            decision_quality="HIGH", rationale=["ok"], evidence_refs=["ev-1"],
            proposal_id="p-1", policy_result_id="pe-1", invalidation_conditions=[], bundle_id="b-1",
        )


def test_veto_requires_auditable_rationale():
    with pytest.raises(ValidationError):
        FinalInvestmentDecision.build(
            decision_id="d-1", decision_time=NOW, valid_from=NOW,
            subject_type="SYMBOL", subject_key="AAA", decision_action="VETO",
            investment_action="BUY", target_weight=None, weight_delta=None, confidence=0.8,
            decision_quality="HIGH", rationale=[], evidence_refs=["ev-1"],
            proposal_id="p-1", policy_result_id="pe-1", invalidation_conditions=[], bundle_id="b-1",
        )


def test_target_and_delta_are_not_ambiguous():
    with pytest.raises(ValidationError):
        proposal(target_weight=0.1, weight_delta=0.01)
    with pytest.raises(ValidationError):
        FinalInvestmentDecision.build(
            decision_id="d-1", decision_time=NOW, valid_from=NOW,
            subject_type="SYMBOL", subject_key="AAA", decision_action="APPROVE",
            investment_action="BUY", target_weight=0.1, weight_delta=0.01, confidence=0.8,
            decision_quality="HIGH", rationale=["ok"], evidence_refs=["ev-1"],
            proposal_id="p-1", policy_result_id="pe-1", invalidation_conditions=[], bundle_id="b-1",
        )
    with pytest.raises(ValidationError):
        FinalInvestmentDecision.build(
            decision_id="d-1", decision_time=NOW, valid_from=NOW,
            subject_type="SYMBOL", subject_key="AAA", decision_action="APPROVE",
            investment_action="BUY", target_weight=None, weight_delta=None, confidence=0.8,
            decision_quality="HIGH", rationale=["ok"], evidence_refs=["ev-1"],
            proposal_id="p-1", policy_result_id="pe-1", invalidation_conditions=[], bundle_id="b-1",
        )


def test_policy_check_semantics_are_strict():
    with pytest.raises(ValidationError):
        PolicyCheckResult(rule_id="R", rule_version="v2", passed=False, severity="INFO", input_snapshot={}, original_value=1, adjusted_value=None, reason_code="bad", reason="bad")
    with pytest.raises(ValidationError):
        PolicyCheckResult(rule_id="R", rule_version="v2", passed=False, severity="ADJUST", input_snapshot={}, original_value=1, adjusted_value=None, reason_code="bad", reason="bad")
