"""Deterministic Policy Engine（详细修改方案 §7）。

LLM 输出 InvestmentProposal -> Policy Engine -> ApprovedDecision。
LLM 不具备风险/交易规则权威：任何提案都必须通过本引擎才能执行。
"""
from __future__ import annotations

from engines.policy.models import ApprovedDecision, InvestmentProposal, PolicyContext
from engines.policy.rules import POLICY_RULES, PolicyLimits

POLICY_ENGINE_VERSION = "policy.v1"

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

    def evaluate(self, proposal: InvestmentProposal, context: PolicyContext | None = None) -> ApprovedDecision:
        context = context or PolicyContext()
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


__all__ = ["PolicyEngine", "POLICY_ENGINE_VERSION", "HARD_REJECTION_RULES", "SOFT_ADJUSTMENT_RULES"]
