from __future__ import annotations


def summarize_theme_exposure(positions: list[dict]) -> dict[str, float]:
    total = sum(float(item.get("market_value", 0)) for item in positions)
    exposure: dict[str, float] = {}
    if total <= 0:
        return exposure
    for item in positions:
        theme = item.get("theme") or "未分类"
        exposure[theme] = exposure.get(theme, 0.0) + float(item.get("market_value", 0)) / total
    return {key: round(value, 4) for key, value in exposure.items()}

