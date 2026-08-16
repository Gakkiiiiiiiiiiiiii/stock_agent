"""Advisory Suitability（详细修改方案 §8）。

投顾建议必须能解释：为什么适合这个用户、为什么仓位只有 X%、什么条件下不再适合。
"""
from __future__ import annotations

from engines.advisory.models import InvestorProfile, Recommendation

RISK_ORDER = {"CONSERVATIVE": 0, "BALANCED": 1, "GROWTH": 2, "AGGRESSIVE": 3}

# 不同风险等级投资者的单标的仓位上限（适当性约束）。
_WEIGHT_CAP_BY_RISK = {"CONSERVATIVE": 0.05, "BALANCED": 0.10, "GROWTH": 0.15, "AGGRESSIVE": 0.25}


def evaluate_suitability(profile: InvestorProfile, recommendation: Recommendation) -> dict:
    """确定性适当性评估：适合 / 有条件适合（降仓）/ 不适合。"""
    reasons: list[str] = []
    rejections: list[str] = []

    if recommendation.market not in profile.allowed_markets:
        rejections.append(f"MARKET_NOT_ALLOWED:{recommendation.market}")
    if recommendation.product not in profile.allowed_products:
        rejections.append(f"PRODUCT_NOT_ALLOWED:{recommendation.product}")
    if RISK_ORDER.get(recommendation.risk_rating, 1) > RISK_ORDER.get(profile.risk_level, 1):
        rejections.append(f"RISK_MISMATCH:product={recommendation.risk_rating}>investor={profile.risk_level}")
    if recommendation.expected_max_drawdown > profile.max_drawdown_tolerance:
        rejections.append(
            f"DRAWDOWN_EXCEEDS_TOLERANCE:{recommendation.expected_max_drawdown}>{profile.max_drawdown_tolerance}"
        )

    weight_cap = _WEIGHT_CAP_BY_RISK.get(profile.risk_level, 0.10)
    if profile.investment_horizon_years < recommendation.holding_horizon_years:
        reasons.append("HORIZON_MISMATCH")
        weight_cap = min(weight_cap, 0.05)
    if profile.liquidity_need == "HIGH" and recommendation.liquidity_profile == "LOW":
        reasons.append("LIQUIDITY_MISMATCH")
        weight_cap = min(weight_cap, 0.05)

    approved_weight = min(recommendation.weight, weight_cap)
    if rejections:
        return {
            "suitable": False,
            "status": "REJECTED",
            "approved_weight": 0.0,
            "rejections": rejections,
            "conditions": reasons,
            "explanation": f"不适合该投资者：{'; '.join(rejections)}",
            "when_no_longer_suitable": [],
        }
    adjusted = approved_weight < recommendation.weight
    return {
        "suitable": True,
        "status": "CONDITIONAL" if adjusted or reasons else "SUITABLE",
        "approved_weight": round(approved_weight, 6),
        "rejections": [],
        "conditions": reasons + (["WEIGHT_CAPPED_BY_RISK_LEVEL"] if adjusted else []),
        "explanation": (
            f"适合 {profile.risk_level} 投资者；仓位上限 {weight_cap}"
            + (f"，建议降仓至 {round(approved_weight, 6)}" if adjusted else "")
        ),
        "when_no_longer_suitable": [
            "投资者风险等级下调",
            "回撤容忍度低于产品预期回撤",
            "投资者流动性需求升高而产品流动性差",
        ],
    }


__all__ = ["evaluate_suitability", "RISK_ORDER", "_WEIGHT_CAP_BY_RISK"]
