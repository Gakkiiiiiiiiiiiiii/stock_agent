from __future__ import annotations

from engines.theme.event_matcher import match_event_to_theme
from engines.theme.stock_mapping import get_theme_related_stocks
from engines.theme.theme_score import rank_themes
from financial_agent.models import ThemeLogic, ThemeScoreInput
from storage.repositories.theme_repository import ThemeRepository

repo = ThemeRepository()


def _normalize_theme_score_input(item: dict) -> dict:
    payload = dict(item)
    if "theme" not in payload:
        payload["theme"] = payload.get("theme_name") or payload.get("name") or payload.get("sector")
    score_aliases = {
        "price_strength": "price_strength_score",
        "price_score": "price_strength_score",
        "volume": "volume_score",
        "fund_flow": "fund_flow_score",
        "news_heat": "news_score",
        "technical": "technical_score",
        "knowledge": "knowledge_score",
        "risk": "risk_score",
    }
    for source, target in score_aliases.items():
        if target not in payload and source in payload:
            payload[target] = payload[source]
    return payload


def search_theme_logic(theme_name: str, include_stocks: bool = True, include_trigger_rules: bool = True) -> dict:
    theme = repo.search(theme_name)
    if not theme:
        return {"theme_name": theme_name, "found": False}
    data = theme.model_dump()
    if not include_stocks:
        data.pop("related_stocks", None)
    if not include_trigger_rules:
        data.pop("trigger_rules", None)
    data["found"] = True
    return data


def get_theme_related_stocks_tool(theme_name: str) -> dict:
    theme = repo.search(theme_name)
    return {"theme_name": theme_name, "related_stocks": [item.model_dump() for item in get_theme_related_stocks(theme)]} if theme else {"theme_name": theme_name, "related_stocks": []}


def upsert_theme_logic(payload: dict) -> dict:
    theme = ThemeLogic.model_validate(payload)
    return {"status": "saved", "theme": repo.upsert(theme).model_dump()}


def evaluate_theme_trigger(theme_name: str, event_title: str, event_content: str = "") -> dict:
    theme = repo.search(theme_name)
    if not theme:
        return {"theme": theme_name, "matched": False, "reason": "theme not found"}
    return match_event_to_theme(event_title, event_content, theme)


def rank_themes_by_score(items: list[dict]) -> dict:
    scores = rank_themes([ThemeScoreInput.model_validate(_normalize_theme_score_input(item)) for item in items])
    return {"top_themes": [item.model_dump() for item in scores]}
