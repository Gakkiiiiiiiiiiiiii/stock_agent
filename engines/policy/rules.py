"""Policy 规则集（详细修改方案 §7）。

规则至少覆盖：single position limit / industry exposure / theme exposure /
liquidity / portfolio drawdown mode / restricted universe / ST & suspension /
minimum evidence / minimum confidence / factor coverage。
"""
from __future__ import annotations

from dataclasses import dataclass

from engines.policy.models import InvestmentProposal, PolicyCheck, PolicyContext


@dataclass(frozen=True)
class PolicyLimits:
    single_position_limit: float = 0.10
    industry_exposure_limit: float = 0.30
    theme_exposure_limit: float = 0.25
    drawdown_mode_scale: float = 0.5
    min_evidence_count: int = 1
    min_confidence: float = 0.3
    min_factor_coverage: float = 0.5


def check_single_position_limit(proposal: InvestmentProposal, context: PolicyContext, limits: PolicyLimits) -> PolicyCheck:
    existing = context.existing_weights.get(proposal.symbol, 0.0)
    cap = limits.single_position_limit
    if context.portfolio_drawdown_mode:
        cap *= limits.drawdown_mode_scale
    allowed = max(0.0, cap - existing)
    if proposal.proposed_weight > allowed:
        return PolicyCheck(
            rule="SINGLE_POSITION_LIMIT", passed=allowed > 0,
            reason=f"proposed {proposal.proposed_weight} > allowed {round(allowed, 6)} (limit {cap}, existing {existing})",
            adjusted_weight=round(allowed, 6),
        )
    return PolicyCheck(rule="SINGLE_POSITION_LIMIT", passed=True)


def check_industry_exposure(proposal: InvestmentProposal, context: PolicyContext, limits: PolicyLimits) -> PolicyCheck:
    if not proposal.sector:
        return PolicyCheck(rule="INDUSTRY_EXPOSURE", passed=True, reason="no sector declared")
    current = context.industry_weights.get(proposal.sector, 0.0)
    allowed = max(0.0, limits.industry_exposure_limit - current)
    if proposal.proposed_weight > allowed:
        return PolicyCheck(
            rule="INDUSTRY_EXPOSURE", passed=False,
            reason=f"industry {proposal.sector} exposure would exceed {limits.industry_exposure_limit}",
            adjusted_weight=round(allowed, 6),
        )
    return PolicyCheck(rule="INDUSTRY_EXPOSURE", passed=True)


def check_theme_exposure(proposal: InvestmentProposal, context: PolicyContext, limits: PolicyLimits) -> PolicyCheck:
    if not proposal.theme:
        return PolicyCheck(rule="THEME_EXPOSURE", passed=True, reason="no theme declared")
    current = context.theme_weights.get(proposal.theme, 0.0)
    allowed = max(0.0, limits.theme_exposure_limit - current)
    if proposal.proposed_weight > allowed:
        return PolicyCheck(
            rule="THEME_EXPOSURE", passed=False,
            reason=f"theme {proposal.theme} exposure would exceed {limits.theme_exposure_limit}",
            adjusted_weight=round(allowed, 6),
        )
    return PolicyCheck(rule="THEME_EXPOSURE", passed=True)


def check_liquidity(proposal: InvestmentProposal, context: PolicyContext, limits: PolicyLimits) -> PolicyCheck:
    if not proposal.liquidity_ok:
        return PolicyCheck(rule="LIQUIDITY", passed=False, reason="liquidity insufficient for proposed weight")
    return PolicyCheck(rule="LIQUIDITY", passed=True)


def check_drawdown_mode(proposal: InvestmentProposal, context: PolicyContext, limits: PolicyLimits) -> PolicyCheck:
    if context.portfolio_drawdown_mode and proposal.proposed_weight > limits.single_position_limit * limits.drawdown_mode_scale:
        return PolicyCheck(
            rule="DRAWDOWN_MODE", passed=False,
            reason="portfolio in drawdown mode: position size must be halved",
            adjusted_weight=round(limits.single_position_limit * limits.drawdown_mode_scale, 6),
        )
    return PolicyCheck(rule="DRAWDOWN_MODE", passed=True)


def check_restricted_universe(proposal: InvestmentProposal, context: PolicyContext, limits: PolicyLimits) -> PolicyCheck:
    if proposal.symbol in set(context.restricted_universe):
        return PolicyCheck(rule="RESTRICTED_UNIVERSE", passed=False, reason=f"{proposal.symbol} is restricted")
    return PolicyCheck(rule="RESTRICTED_UNIVERSE", passed=True)


def check_st_suspension(proposal: InvestmentProposal, context: PolicyContext, limits: PolicyLimits) -> PolicyCheck:
    if proposal.action == "BUY" and (proposal.is_st or proposal.is_suspended):
        state = "ST" if proposal.is_st else "SUSPENDED"
        return PolicyCheck(rule="ST_SUSPENSION", passed=False, reason=f"cannot BUY {state} security")
    return PolicyCheck(rule="ST_SUSPENSION", passed=True)


def check_minimum_evidence(proposal: InvestmentProposal, context: PolicyContext, limits: PolicyLimits) -> PolicyCheck:
    if proposal.evidence_count < limits.min_evidence_count:
        return PolicyCheck(rule="MINIMUM_EVIDENCE", passed=False, reason=f"evidence_count {proposal.evidence_count} < {limits.min_evidence_count}")
    return PolicyCheck(rule="MINIMUM_EVIDENCE", passed=True)


def check_minimum_confidence(proposal: InvestmentProposal, context: PolicyContext, limits: PolicyLimits) -> PolicyCheck:
    if proposal.confidence < limits.min_confidence:
        return PolicyCheck(rule="MINIMUM_CONFIDENCE", passed=False, reason=f"confidence {proposal.confidence} < {limits.min_confidence}")
    return PolicyCheck(rule="MINIMUM_CONFIDENCE", passed=True)


def check_factor_coverage(proposal: InvestmentProposal, context: PolicyContext, limits: PolicyLimits) -> PolicyCheck:
    if proposal.factor_coverage < limits.min_factor_coverage:
        return PolicyCheck(rule="FACTOR_COVERAGE", passed=False, reason=f"factor coverage {proposal.factor_coverage} < {limits.min_factor_coverage}")
    return PolicyCheck(rule="FACTOR_COVERAGE", passed=True)


POLICY_RULES = (
    check_restricted_universe,
    check_st_suspension,
    check_minimum_evidence,
    check_minimum_confidence,
    check_factor_coverage,
    check_liquidity,
    check_drawdown_mode,
    check_single_position_limit,
    check_industry_exposure,
    check_theme_exposure,
)


__all__ = ["PolicyLimits", "POLICY_RULES"]
