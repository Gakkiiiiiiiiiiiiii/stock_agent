from __future__ import annotations

from datetime import UTC, datetime

from engines.memory.memory_extractor import extract_memory
from engines.memory.memory_writer import write_memory_and_enqueue
from financial_agent.models import ThemeLogic
from storage.repositories.theme_repository import ThemeRepository

repo = ThemeRepository()


def search_theme_logic(theme_name: str, include_stocks: bool = True, include_trigger_rules: bool = True) -> dict:
    theme = repo.search(theme_name)
    if not theme:
        return {"theme_name": theme_name, "found": False}
    payload = theme.model_dump()
    if not include_stocks:
        payload.pop("related_stocks", None)
    if not include_trigger_rules:
        payload.pop("trigger_rules", None)
    return {"found": True, **payload}


def upsert_theme_logic(payload: dict) -> dict:
    theme = repo.upsert(ThemeLogic.model_validate(payload))
    memory_payload = extract_memory(
        title=f"主题逻辑 {theme.theme_name}",
        content=theme.core_thesis or theme.theme_name,
        memory_type="industry_logic",
    ) | {
        "source_type": "theme_logic",
        "source_date": datetime.now(UTC),
        "related_theme": theme.theme_name,
    }
    index_result = write_memory_and_enqueue(memory_payload, target_collection="financial_knowledge")
    return {"status": "saved", "theme": theme.model_dump(), "index_result": index_result}
