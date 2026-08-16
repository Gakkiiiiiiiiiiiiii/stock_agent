"""Policy Engine 数据模型（详细修改方案 §7）。

LLM 只产生 InvestmentProposal；最终可执行决策必须由确定性 Policy Engine 批准。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


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
