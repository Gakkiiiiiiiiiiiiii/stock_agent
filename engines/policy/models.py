"""Policy Engine 数据模型（详细修改方案 §7）。

LLM 只产生 InvestmentProposal；最终可执行决策必须由确定性 Policy Engine 批准。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class InvestmentProposal:
    """LLM/专家产出的投资提案（不是最终决策）。"""

    symbol: str
    action: str  # BUY / SELL / HOLD
    proposed_weight: float
    confidence: float = 0.0
    thesis_refs: list[str] = field(default_factory=list)
    sector: str | None = None
    theme: str | None = None
    evidence_count: int = 0
    factor_coverage: float = 1.0
    domain_coverage: dict[str, float] = field(default_factory=dict)
    liquidity_ok: bool = True
    is_st: bool = False
    is_suspended: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PolicyContext:
    """策略上下文：组合状态与市场约束。"""

    portfolio_drawdown_mode: bool = False
    restricted_universe: list[str] = field(default_factory=list)
    existing_weights: dict[str, float] = field(default_factory=dict)
    industry_weights: dict[str, float] = field(default_factory=dict)
    theme_weights: dict[str, float] = field(default_factory=dict)
    domain_coverage: dict[str, float] = field(default_factory=dict)

    # v2 deterministic-policy inputs.  The defaults preserve the v1 API and
    # are only consulted when a v2 proposal is evaluated.
    gross_exposure: float | None = None
    net_exposure: float | None = None
    portfolio_available: bool | None = None
    portfolio_snapshot_at: datetime | None = None
    portfolio_snapshot_fresh: bool | None = None
    portfolio_snapshot_freshness_seconds: float | None = None
    max_portfolio_snapshot_age_seconds: float = 86400.0
    dependency_health: dict[str, Any] = field(default_factory=dict)
    dependency_status: dict[str, Any] = field(default_factory=dict)
    evidence_freshness_seconds: float | None = None
    evidence_fresh: bool | None = None
    max_evidence_age_seconds: float = 86400.0
    risk_veto: bool = False
    risk_veto_reason: str = ""
    subject_sectors: dict[str, str] = field(default_factory=dict)
    subject_themes: dict[str, str] = field(default_factory=dict)
    liquidity_ok: dict[str, bool] = field(default_factory=dict)
    security_is_st: dict[str, bool] = field(default_factory=dict)
    security_is_suspended: dict[str, bool] = field(default_factory=dict)
    required_domains: list[str] = field(default_factory=lambda: ["market", "factor", "risk"])
    # Dependency requirements are supplied by the active skill.  Quant is the
    # only mandatory dependency for actionable proposals by default; factor
    # and content degradation is represented in the trace unless required by
    # the skill.
    required_dependencies: list[str] = field(default_factory=list)
    skill_required_dependencies: list[str] = field(default_factory=list)
    dependency_required: dict[str, bool] = field(default_factory=dict)
    security_facts_verified: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PolicyCheck:
    rule: str
    passed: bool
    reason: str = ""
    adjusted_weight: float | None = None


@dataclass
class ApprovedDecision:
    """Policy Engine 的确定性输出。"""

    approved: bool
    approved_weight: float = 0.0
    adjustments: list[str] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)
    checks: list[PolicyCheck] = field(default_factory=list)
    policy_version: str = "policy.v1"

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["checks"] = [asdict(check) for check in self.checks]
        return payload


__all__ = ["InvestmentProposal", "PolicyContext", "PolicyCheck", "ApprovedDecision"]
