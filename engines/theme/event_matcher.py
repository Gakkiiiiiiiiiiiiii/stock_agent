from __future__ import annotations

from financial_agent.models import ThemeLogic


def match_event_to_theme(title: str, content: str, theme: ThemeLogic) -> dict[str, object]:
    text = f"{title} {content}".lower()
    hits = [keyword for keyword in theme.monitor_keywords if keyword.lower() in text]
    catalyst_hits = [item for item in theme.catalysts if item.lower() in text]
    score = min(100, len(hits) * 15 + len(catalyst_hits) * 20)
    return {
        "theme": theme.theme_name,
        "matched": score > 0,
        "score": score,
        "keywords": hits,
        "catalysts": catalyst_hits,
    }

