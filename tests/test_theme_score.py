from engines.theme.theme_score import score_theme
from financial_agent.models import ThemeScoreInput


def test_theme_score_formula_caps_range():
    result = score_theme(
        ThemeScoreInput(
            theme="AI机房液冷",
            price_strength_score=100,
            volume_score=100,
            fund_flow_score=100,
            news_score=100,
            technical_score=100,
            knowledge_score=100,
            risk_score=100,
        )
    )
    assert result.score == 80
    assert "theme" not in result.components

