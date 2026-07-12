from __future__ import annotations

from financial_agent.models import ThemeLogic, ThemeStock


def get_theme_related_stocks(theme: ThemeLogic) -> list[ThemeStock]:
    return sorted(theme.related_stocks, key=lambda item: (item.sensitivity_score, item.certainty_score), reverse=True)

