"""Policy 决策解释（详细修改方案 §7/§8）。

投顾建议必须能解释：为什么批准/拒绝、为什么仓位是这个值、什么条件下不再适合。
"""
from __future__ import annotations

from engines.policy.models import ApprovedDecision, InvestmentProposal


def explain_decision(proposal: InvestmentProposal, decision: ApprovedDecision) -> dict:
    approval_reasons = [check.rule for check in decision.checks if check.passed]
    return {
        "symbol": proposal.symbol,
        "action": proposal.action,
        "approved": decision.approved,
        "approved_weight": decision.approved_weight,
        "proposed_weight": proposal.proposed_weight,
        "policy_version": decision.policy_version,
        "why": (
            f"提案 {proposal.symbol} {proposal.action} {proposal.proposed_weight}："
            + (f"批准仓位 {decision.approved_weight}（调整：{', '.join(decision.adjustments)}）" if decision.approved else f"拒绝（{', '.join(decision.rejections)}）")
        ),
        "checks_passed": approval_reasons,
        "adjustments": decision.adjustments,
        "rejections": decision.rejections,
        "when_no_longer_suitable": [
            "证据数量低于最低要求",
            "置信度低于阈值",
            "组合进入回撤模式且仓位超限",
            "标的进入限制池 / ST / 停牌",
        ],
    }


__all__ = ["explain_decision"]
