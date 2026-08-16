"""Advisory 模型（详细修改方案 §8）。

投顾适当性 bounded context：投资者画像 + 投顾政策快照。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

RISK_LEVELS = ("CONSERVATIVE", "BALANCED", "GROWTH", "AGGRESSIVE")


@dataclass(frozen=True)
class InvestorProfile:
    risk_level: str = "BALANCED"
    investment_horizon_years: float = 3.0
    liquidity_need: str = "MEDIUM"  # LOW / MEDIUM / HIGH
    max_drawdown_tolerance: float = 0.15
    allowed_markets: tuple[str, ...] = ("CN_A",)
    allowed_products: tuple[str, ...] = ("EQUITY",)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AdvisoryPolicySnapshot:
    policy_snapshot_id: str
    policy_version: str
    investor_profile_version: str
    restrictions: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Recommendation:
    """待适当性审查的投顾建议。"""

    symbol: str
    action: str
    weight: float
    market: str = "CN_A"
    product: str = "EQUITY"
    risk_rating: str = "BALANCED"  # 产品风险等级
    expected_max_drawdown: float = 0.10
    holding_horizon_years: float = 1.0
    liquidity_profile: str = "HIGH"  # 产品流动性

    def to_dict(self) -> dict:
        return asdict(self)


__all__ = ["RISK_LEVELS", "InvestorProfile", "AdvisoryPolicySnapshot", "Recommendation"]
