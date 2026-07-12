from __future__ import annotations

from collections import defaultdict

from financial_agent.models import Position, RiskReview


def evaluate_portfolio_risk(positions: list[Position], max_single_weight: float = 0.2, max_theme_weight: float = 0.4) -> RiskReview:
    total = sum(item.market_value for item in positions)
    if total <= 0:
        return RiskReview(total_market_value=0, concentration=[], theme_exposure=[], warnings=["持仓为空"], suggested_position="0%-20%")
    concentration = []
    warnings = []
    theme_values: dict[str, float] = defaultdict(float)
    for item in positions:
        weight = item.market_value / total
        concentration.append({"symbol": item.symbol, "name": item.name, "weight": round(weight, 4)})
        theme_values[item.theme or "未分类"] += item.market_value
        if weight > max_single_weight:
            warnings.append(f"{item.symbol} 单票权重 {weight:.1%} 超过上限")
    theme_exposure = []
    for theme, value in theme_values.items():
        weight = value / total
        theme_exposure.append({"theme": theme, "weight": round(weight, 4)})
        if weight > max_theme_weight:
            warnings.append(f"{theme} 主题暴露 {weight:.1%} 超过上限")
    suggested_position = "50%-70%" if not warnings else "30%-50%"
    return RiskReview(
        total_market_value=total,
        concentration=sorted(concentration, key=lambda item: item["weight"], reverse=True),
        theme_exposure=sorted(theme_exposure, key=lambda item: item["weight"], reverse=True),
        warnings=warnings,
        suggested_position=suggested_position,
    )

