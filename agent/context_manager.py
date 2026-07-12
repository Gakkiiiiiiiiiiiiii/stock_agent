from __future__ import annotations


def build_agent_context(task_type: str, query: str, extra: dict | None = None) -> dict:
    return {"task_type": task_type, "query": query, "extra": extra or {}}

