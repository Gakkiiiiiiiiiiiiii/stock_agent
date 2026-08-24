"""Policy 规则集（详细修改方案 §7）。

规则至少覆盖：single position limit / industry exposure / theme exposure /
liquidity / portfolio drawdown mode / restricted universe / ST & suspension /
minimum evidence / minimum confidence / factor coverage。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts.decision import PolicyCheckResult
from contracts.proposal import InvestmentProposalV2
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
    portfolio_gross_limit: float = 1.0
    portfolio_net_limit: float = 1.0
    max_evidence_age_seconds: float = 86400.0
    max_portfolio_snapshot_age_seconds: float = 86400.0
    min_domain_coverage: float = 0.5


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


V2_POLICY_RULE_IDS = (
    "SINGLE_POSITION_LIMIT",
    "PORTFOLIO_GROSS_LIMIT",
    "PORTFOLIO_NET_LIMIT",
    "INDUSTRY_EXPOSURE",
    "THEME_EXPOSURE",
    "LIQUIDITY",
    "DRAWDOWN_MODE",
    "RESTRICTED_UNIVERSE",
    "ST_SUSPENSION",
    "MINIMUM_EVIDENCE",
    "MINIMUM_CONFIDENCE",
    "FACTOR_COVERAGE",
    "DEPENDENCY_HEALTH",
    "EVIDENCE_FRESHNESS",
    "PORTFOLIO_SNAPSHOT_FRESHNESS",
    "RISK_VETO",
)


def _v2_check(
    rule_id: str,
    *,
    passed: bool,
    severity: str,
    original: Any,
    adjusted: Any,
    reason_code: str,
    reason: str,
    proposal: InvestmentProposalV2,
    context: PolicyContext,
    rule_version: str = "v2",
) -> PolicyCheckResult:
    snapshot = {
        "proposal_id": proposal.proposal_id,
        "subject_type": proposal.subject_type,
        "subject_key": proposal.subject_key,
        "action": proposal.action,
        "target_weight": proposal.target_weight,
        "weight_delta": proposal.weight_delta,
        "current_weight": context.existing_weights.get(proposal.subject_key or "", 0.0),
    }
    return PolicyCheckResult(
        rule_id=rule_id,
        rule_version=rule_version,
        passed=passed,
        severity=severity,
        input_snapshot=snapshot,
        original_value=original,
        adjusted_value=adjusted,
        reason_code=reason_code,
        reason=reason,
    )


def _v2_target(proposal: InvestmentProposalV2, current_weight: float) -> float | None:
    if proposal.target_weight is not None:
        return proposal.target_weight
    if proposal.weight_delta is not None:
        return max(0.0, current_weight + proposal.weight_delta)
    return None


def evaluate_v2_checks(
    proposal: InvestmentProposalV2,
    context: PolicyContext,
    limits: PolicyLimits,
    *,
    policy_version: str = "policy.v2",
) -> tuple[list[PolicyCheckResult], float | None]:
    """Evaluate v2 rules in fixed order, carrying each adjustment forward."""
    current = context.existing_weights.get(proposal.subject_key or "", 0.0)
    target = _v2_target(proposal, current)
    actionable = target is not None
    checks: list[PolicyCheckResult] = []

    def add(rule_id: str, *, passed: bool, severity: str, original: Any, adjusted: Any,
            reason_code: str, reason: str) -> None:
        check = _v2_check(rule_id, passed=passed, severity=severity, original=original,
                          adjusted=adjusted, reason_code=reason_code, reason=reason,
                          proposal=proposal, context=context, rule_version=f"{policy_version}.1")
        checks.append(check)

    # Position and exposure limits are deliberately evaluated in this order;
    # the resulting target is the input to the next adjustment.
    if target is None:
        add("SINGLE_POSITION_LIMIT", passed=True, severity="INFO", original=None, adjusted=None,
            reason_code="NOT_ACTIONABLE", reason="proposal has no target weight")
    else:
        existing = context.existing_weights.get(proposal.subject_key or "", 0.0)
        # DRAWDOWN_MODE is a separate ordered rule below; keeping the base
        # single-position cap here makes both checks observable in the trace.
        cap = limits.single_position_limit
        allowed = max(0.0, cap)
        adjusted = min(target, allowed)
        add("SINGLE_POSITION_LIMIT", passed=target <= allowed, severity="INFO" if target <= allowed else "ADJUST",
            original=target, adjusted=adjusted, reason_code="SINGLE_POSITION_LIMIT_OK" if target <= allowed else "SINGLE_POSITION_LIMIT_EXCEEDED",
            reason="single position is within limit" if target <= allowed else f"target {target} exceeds limit {allowed}")
        target = adjusted

    gross = context.gross_exposure
    if target is None or gross is None:
        add("PORTFOLIO_GROSS_LIMIT", passed=not actionable or gross is not None, severity="INFO" if not actionable or gross is not None else "REJECT",
            original=gross, adjusted=gross, reason_code="PORTFOLIO_GROSS_OK" if not actionable or gross is not None else "PORTFOLIO_SNAPSHOT_MISSING",
            reason="gross exposure available or proposal is non-actionable" if not actionable or gross is not None else "portfolio snapshot is required for target weight")
    else:
        projected = gross - context.existing_weights.get(proposal.subject_key or "", 0.0) + target
        allowed = max(0.0, limits.portfolio_gross_limit - (gross - context.existing_weights.get(proposal.subject_key or "", 0.0)))
        adjusted = min(target, allowed)
        add("PORTFOLIO_GROSS_LIMIT", passed=projected <= limits.portfolio_gross_limit, severity="INFO" if projected <= limits.portfolio_gross_limit else "ADJUST",
            original=target, adjusted=adjusted, reason_code="PORTFOLIO_GROSS_OK" if projected <= limits.portfolio_gross_limit else "PORTFOLIO_GROSS_EXCEEDED",
            reason="gross exposure is within limit" if projected <= limits.portfolio_gross_limit else "target reduced to portfolio gross headroom")
        target = adjusted

    net = context.net_exposure
    if target is None or net is None:
        add("PORTFOLIO_NET_LIMIT", passed=not actionable or net is not None, severity="INFO" if not actionable or net is not None else "REJECT",
            original=net, adjusted=net, reason_code="PORTFOLIO_NET_OK" if not actionable or net is not None else "PORTFOLIO_SNAPSHOT_MISSING",
            reason="net exposure available or proposal is non-actionable" if not actionable or net is not None else "portfolio snapshot is required for target weight")
    else:
        projected = net + target - context.existing_weights.get(proposal.subject_key or "", 0.0)
        allowed = max(0.0, limits.portfolio_net_limit - (net - context.existing_weights.get(proposal.subject_key or "", 0.0)))
        adjusted = min(target, allowed)
        add("PORTFOLIO_NET_LIMIT", passed=projected <= limits.portfolio_net_limit, severity="INFO" if projected <= limits.portfolio_net_limit else "ADJUST",
            original=target, adjusted=adjusted, reason_code="PORTFOLIO_NET_OK" if projected <= limits.portfolio_net_limit else "PORTFOLIO_NET_EXCEEDED",
            reason="net exposure is within limit" if projected <= limits.portfolio_net_limit else "target reduced to portfolio net headroom")
        target = adjusted

    sector = context.subject_sectors.get(proposal.subject_key or "")
    current_industry = context.industry_weights.get(sector, 0.0) if sector else 0.0
    industry_allowed = max(0.0, limits.industry_exposure_limit - current_industry)
    if target is None or not sector:
        add("INDUSTRY_EXPOSURE", passed=True, severity="INFO", original=target, adjusted=target,
            reason_code="INDUSTRY_EXPOSURE_OK", reason="no industry exposure supplied")
    else:
        adjusted = min(target, industry_allowed)
        add("INDUSTRY_EXPOSURE", passed=target <= industry_allowed, severity="INFO" if target <= industry_allowed else "ADJUST",
            original=target, adjusted=adjusted, reason_code="INDUSTRY_EXPOSURE_OK" if target <= industry_allowed else "INDUSTRY_EXPOSURE_EXCEEDED",
            reason="industry exposure is within limit" if target <= industry_allowed else "target reduced to industry headroom")
        target = adjusted

    theme = context.subject_themes.get(proposal.subject_key or "")
    current_theme = context.theme_weights.get(theme, 0.0) if theme else 0.0
    theme_allowed = max(0.0, limits.theme_exposure_limit - current_theme)
    if target is None or not theme:
        add("THEME_EXPOSURE", passed=True, severity="INFO", original=target, adjusted=target,
            reason_code="THEME_EXPOSURE_OK", reason="no theme exposure supplied")
    else:
        adjusted = min(target, theme_allowed)
        add("THEME_EXPOSURE", passed=target <= theme_allowed, severity="INFO" if target <= theme_allowed else "ADJUST",
            original=target, adjusted=adjusted, reason_code="THEME_EXPOSURE_OK" if target <= theme_allowed else "THEME_EXPOSURE_EXCEEDED",
            reason="theme exposure is within limit" if target <= theme_allowed else "target reduced to theme headroom")
        target = adjusted

    subject_key = proposal.subject_key or ""
    security_actionable = proposal.subject_type == "SYMBOL" and proposal.action in {"BUY", "INCREASE"}
    liquidity_known = subject_key in context.liquidity_ok
    liquidity_ok = context.liquidity_ok.get(subject_key)
    # A broad ``facts_verified`` marker is not evidence for a distinct
    # liquidity fact.  Actionable symbols must carry an explicit, subject-
    # scoped liquidity_ok=True value.
    liquidity_pass = (not security_actionable) or (liquidity_known and liquidity_ok is True)
    liquidity_reason = "liquidity is sufficient" if liquidity_pass else "liquidity capacity is missing or insufficient"
    add("LIQUIDITY", passed=liquidity_pass, severity="INFO" if liquidity_pass else "REJECT", original=liquidity_ok, adjusted=liquidity_ok,
        reason_code="LIQUIDITY_OK" if liquidity_pass else "LIQUIDITY_UNKNOWN_OR_INSUFFICIENT", reason=liquidity_reason)
    drawdown_cap = limits.single_position_limit * limits.drawdown_mode_scale
    if target is None or not context.portfolio_drawdown_mode:
        add("DRAWDOWN_MODE", passed=True, severity="INFO", original=target, adjusted=target,
            reason_code="DRAWDOWN_MODE_OK", reason="portfolio is not in drawdown mode")
    else:
        adjusted = min(target, drawdown_cap)
        add("DRAWDOWN_MODE", passed=target <= drawdown_cap, severity="INFO" if target <= drawdown_cap else "ADJUST",
            original=target, adjusted=adjusted, reason_code="DRAWDOWN_MODE_OK" if target <= drawdown_cap else "DRAWDOWN_MODE_ADJUSTED",
            reason="drawdown position is within cap" if target <= drawdown_cap else "target reduced for drawdown mode")
        target = adjusted

    restricted = proposal.subject_key in set(context.restricted_universe)
    add("RESTRICTED_UNIVERSE", passed=not restricted, severity="INFO" if not restricted else "REJECT", original=target, adjusted=target,
        reason_code="UNIVERSE_ALLOWED" if not restricted else "UNIVERSE_RESTRICTED", reason="subject is allowed" if not restricted else "subject is restricted")
    st_known = subject_key in context.security_is_st
    suspended_known = subject_key in context.security_is_suspended
    is_st = context.security_is_st.get(subject_key)
    is_suspended = context.security_is_suspended.get(subject_key)
    blocked_state = security_actionable and ((is_st is True) or (is_suspended is True))
    # ST and suspension are independent safety facts; verified market data
    # cannot substitute for either missing field.
    security_state_known = (not security_actionable) or (st_known and suspended_known)
    state_pass = security_state_known and not blocked_state
    add("ST_SUSPENSION", passed=state_pass, severity="INFO" if state_pass else "REJECT", original={"is_st": is_st, "is_suspended": is_suspended}, adjusted={"is_st": is_st, "is_suspended": is_suspended},
        reason_code="SECURITY_TRADABLE" if state_pass else ("SECURITY_STATE_UNKNOWN" if not security_state_known else "ST_OR_SUSPENDED"), reason="security is tradable" if state_pass else ("ST/suspension facts are missing" if not security_state_known else "cannot increase a ST or suspended security"))

    evidence_count = len(proposal.evidence_refs)
    coverage = getattr(context, "domain_coverage", {}) or {}
    required_domains = list(context.required_domains)
    missing_domains = [domain for domain in required_domains if coverage.get(domain, 0.0) < limits.min_domain_coverage]
    evidence_ok = evidence_count >= limits.min_evidence_count and not missing_domains
    add("MINIMUM_EVIDENCE", passed=evidence_ok, severity="INFO" if evidence_ok else "REJECT", original=evidence_count, adjusted=evidence_count,
        reason_code="MINIMUM_EVIDENCE_OK" if evidence_ok else "MINIMUM_DOMAIN_COVERAGE_MISSING", reason=f"evidence refs: {evidence_count}; missing domains: {', '.join(missing_domains) or 'none'}")
    confidence_ok = proposal.confidence >= limits.min_confidence
    add("MINIMUM_CONFIDENCE", passed=confidence_ok, severity="INFO" if confidence_ok else "REJECT", original=proposal.confidence, adjusted=proposal.confidence,
        reason_code="CONFIDENCE_OK" if confidence_ok else "CONFIDENCE_TOO_LOW", reason=f"confidence: {proposal.confidence}")
    factor_coverage = coverage.get("factor", coverage.get("FACTOR", None))
    if factor_coverage is None:
        factor_coverage = coverage.get("factor_coverage")
    factor_required = "factor" in required_domains or "factor" in context.required_dependencies or "factor" in context.skill_required_dependencies or context.dependency_required.get("factor", False)
    factor_ok = factor_coverage is not None and factor_coverage >= limits.min_factor_coverage
    if factor_required:
        add("FACTOR_COVERAGE", passed=factor_ok, severity="INFO" if factor_ok else "REJECT", original=factor_coverage, adjusted=factor_coverage,
            reason_code="FACTOR_COVERAGE_OK" if factor_ok else "FACTOR_COVERAGE_LOW", reason=f"required factor coverage: {factor_coverage}")
    else:
        # A skill which does not require factor evidence may proceed with an
        # explicit degraded trace instead of fabricating full coverage.
        add("FACTOR_COVERAGE", passed=True, severity="INFO" if factor_ok else "WARN", original=factor_coverage, adjusted=factor_coverage,
            reason_code="FACTOR_COVERAGE_OK" if factor_ok else "FACTOR_COVERAGE_DEGRADED", reason=f"optional factor coverage: {factor_coverage}")

    statuses: dict[str, Any] = {**context.dependency_health}
    if isinstance(context.dependency_status, dict):
        statuses.update(context.dependency_status)
    else:
        for item in context.dependency_status or []:
            if isinstance(item, dict):
                key = item.get("system") or item.get("source_system")
            else:
                key = getattr(item, "system", None)
            key = getattr(key, "value", key)
            if key:
                statuses[str(key)] = item
    def status_value(value: Any) -> str:
        if hasattr(value, "status"):
            value = value.status
        if isinstance(value, (list, tuple)):
            return ",".join(status_value(item) for item in value)
        return str(getattr(value, "value", value)).upper()
    normalized = {key: status_value(value) for key, value in statuses.items()}
    critical = {"quant"}
    skill_required = set(context.required_dependencies) | set(context.skill_required_dependencies)
    skill_required.update(key for key, required in context.dependency_required.items() if required)
    required = critical | skill_required if actionable else skill_required
    missing_required = sorted(key for key in required if key not in normalized)
    unhealthy_required = sorted(key for key in required if normalized.get(key) not in {"OK", "HEALTHY", "AVAILABLE", "TRUE", "VERIFIED"})
    # Optional factor/content degradation is preserved as a non-blocking
    # status.  A skill can opt into hard enforcement through the fields above.
    dep_ok = not missing_required and not unhealthy_required
    dep_reason = "required dependencies healthy" if dep_ok else f"missing={','.join(missing_required)} unhealthy={','.join(unhealthy_required)}"
    add("DEPENDENCY_HEALTH", passed=dep_ok, severity="INFO" if dep_ok else "REJECT", original=normalized, adjusted=normalized,
        reason_code="DEPENDENCIES_HEALTHY" if dep_ok else "DEPENDENCY_UNHEALTHY_OR_MISSING", reason=dep_reason)

    fresh = context.evidence_fresh
    if context.evidence_freshness_seconds is not None:
        fresh = context.evidence_freshness_seconds <= min(context.max_evidence_age_seconds, limits.max_evidence_age_seconds)
    elif actionable and proposal.evidence_refs:
        fresh = False
    evidence_fresh_ok = bool(fresh) if fresh is not None else True
    add("EVIDENCE_FRESHNESS", passed=evidence_fresh_ok, severity="INFO" if evidence_fresh_ok else "REJECT", original=context.evidence_freshness_seconds, adjusted=context.evidence_freshness_seconds,
        reason_code="EVIDENCE_FRESH" if evidence_fresh_ok else "EVIDENCE_STALE_OR_UNKNOWN", reason="evidence is fresh" if evidence_fresh_ok else "evidence freshness is stale or unavailable")

    portfolio_ok = context.portfolio_snapshot_fresh
    if context.portfolio_snapshot_freshness_seconds is not None:
        portfolio_ok = context.portfolio_snapshot_freshness_seconds <= min(context.max_portfolio_snapshot_age_seconds, limits.max_portfolio_snapshot_age_seconds)
    if context.portfolio_available is False or (actionable and context.portfolio_available is None and context.portfolio_snapshot_at is None):
        portfolio_ok = False
    if not actionable:
        portfolio_ok = True
    add("PORTFOLIO_SNAPSHOT_FRESHNESS", passed=bool(portfolio_ok), severity="INFO" if portfolio_ok else "REJECT", original=context.portfolio_snapshot_freshness_seconds, adjusted=context.portfolio_snapshot_freshness_seconds,
        reason_code="PORTFOLIO_SNAPSHOT_FRESH" if portfolio_ok else "PORTFOLIO_SNAPSHOT_MISSING_OR_STALE", reason="portfolio snapshot is fresh" if portfolio_ok else "fresh portfolio snapshot is required for target weight")

    risk_veto = bool(context.risk_veto)
    add("RISK_VETO", passed=not risk_veto, severity="INFO" if not risk_veto else "REJECT", original=risk_veto, adjusted=risk_veto,
        reason_code="RISK_CLEAR" if not risk_veto else "RISK_VETO", reason="risk specialist did not veto" if not risk_veto else (context.risk_veto_reason or "risk specialist vetoed proposal"))
    return checks, target


__all__ = ["PolicyLimits", "POLICY_RULES", "V2_POLICY_RULE_IDS", "evaluate_v2_checks"]
