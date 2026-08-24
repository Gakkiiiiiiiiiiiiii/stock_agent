"""Deterministic Policy Engine（详细修改方案 §7）。

LLM 输出 InvestmentProposal -> Policy Engine -> ApprovedDecision。
LLM 不具备风险/交易规则权威：任何提案都必须通过本引擎才能执行。
"""
from __future__ import annotations

from contracts.decision import PolicyEvaluation
from contracts.proposal import InvestmentProposalV2
from engines.policy.models import ApprovedDecision, InvestmentProposal, PolicyContext
from engines.policy.rules import POLICY_RULES, PolicyLimits, evaluate_v2_checks

POLICY_ENGINE_VERSION = "policy.v1"
POLICY_ENGINE_V2_VERSION = "policy.v2"

# 硬性否决规则：命中即整体拒绝（不做降权）。
HARD_REJECTION_RULES = frozenset(
    {"RESTRICTED_UNIVERSE", "ST_SUSPENSION", "MINIMUM_EVIDENCE", "MINIMUM_CONFIDENCE", "FACTOR_COVERAGE", "LIQUIDITY"}
)
# 可降权规则：命中时按 adjusted_weight 缩减仓位。
SOFT_ADJUSTMENT_RULES = frozenset({"SINGLE_POSITION_LIMIT", "INDUSTRY_EXPOSURE", "THEME_EXPOSURE", "DRAWDOWN_MODE"})


class PolicyEngine:
    def __init__(self, limits: PolicyLimits | None = None, policy_version: str = POLICY_ENGINE_VERSION) -> None:
        self.limits = limits or PolicyLimits()
        self.policy_version = policy_version

    def evaluate(self, proposal: InvestmentProposal | InvestmentProposalV2, context: PolicyContext | None = None) -> ApprovedDecision | PolicyEvaluation:
        context = context or PolicyContext()
        if isinstance(proposal, InvestmentProposalV2):
            checks, adjusted = evaluate_v2_checks(
                proposal, context, self.limits, policy_version=self.policy_version if self.policy_version != POLICY_ENGINE_VERSION else POLICY_ENGINE_V2_VERSION,
            )
            has_reject = any(check.severity == "REJECT" and not check.passed for check in checks)
            actionable = proposal.target_weight is not None or proposal.weight_delta is not None
            reduction = proposal.action in {"SELL", "REDUCE", "EXIT"}
            if not actionable:
                weight_valid = True
            elif reduction:
                weight_valid = adjusted is not None and adjusted >= 0
            else:
                weight_valid = adjusted is not None and adjusted > 0
            approved = not has_reject and weight_valid
            return PolicyEvaluation.build(
                policy_result_id=f"pe-{proposal.proposal_id}-{self.policy_version if self.policy_version != POLICY_ENGINE_VERSION else POLICY_ENGINE_V2_VERSION}",
                policy_version=self.policy_version if self.policy_version != POLICY_ENGINE_VERSION else POLICY_ENGINE_V2_VERSION,
                checks=checks,
                approved=approved,
                original_value=proposal.target_weight if proposal.target_weight is not None else proposal.weight_delta,
                adjusted_value=adjusted,
            )
        checks = [rule(proposal, context, self.limits) for rule in POLICY_RULES]
        rejections = [check.rule for check in checks if not check.passed and check.rule in HARD_REJECTION_RULES]
        # 软规则：只要给出了更低的 adjusted_weight 即视为降权调整（无论 passed）。
        adjustments = [
            check.rule
            for check in checks
            if check.rule in SOFT_ADJUSTMENT_RULES
            and check.adjusted_weight is not None
            and check.adjusted_weight < proposal.proposed_weight
        ]

        approved_weight = float(proposal.proposed_weight)
        for check in checks:
            if check.rule in SOFT_ADJUSTMENT_RULES and check.adjusted_weight is not None:
                approved_weight = min(approved_weight, check.adjusted_weight)

        approved = not rejections and approved_weight > 0
        return ApprovedDecision(
            approved=approved,
            approved_weight=round(approved_weight, 6) if approved else 0.0,
            adjustments=adjustments,
            rejections=rejections,
            checks=checks,
            policy_version=self.policy_version,
        )


__all__ = ["PolicyEngine", "POLICY_ENGINE_VERSION", "POLICY_ENGINE_V2_VERSION", "HARD_REJECTION_RULES", "SOFT_ADJUSTMENT_RULES"]
