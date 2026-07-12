from __future__ import annotations

from financial_agent.models import ThemeScoreInput, ThemeScoreResult


WEIGHTS = {
    "price_strength_score": 0.25,
    "volume_score": 0.15,
    "fund_flow_score": 0.15,
    "news_score": 0.20,
    "technical_score": 0.15,
    "knowledge_score": 0.10,
    "risk_score": -0.20,
}


def score_theme(item: ThemeScoreInput) -> ThemeScoreResult:
    components = item.model_dump(exclude={"theme"})
    score = sum(components[name] * weight for name, weight in WEIGHTS.items())
    score = max(0.0, min(100.0, round(score, 2)))
    reasons = []
    if item.news_score >= 70:
        reasons.append("新闻/事件催化较强")
    if item.technical_score >= 70:
        reasons.append("核心标的技术形态较强")
    if item.risk_score >= 60:
        reasons.append("但拥挤或证伪风险偏高")
    if not reasons:
        reasons.append("各分项处于中性区间")
    return ThemeScoreResult(theme=item.theme, score=score, components=components, reason=" + ".join(reasons))


def rank_themes(items: list[ThemeScoreInput]) -> list[ThemeScoreResult]:
    return sorted((score_theme(item) for item in items), key=lambda item: item.score, reverse=True)

