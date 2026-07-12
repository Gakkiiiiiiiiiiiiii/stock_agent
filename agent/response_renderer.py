from __future__ import annotations


def render_response(report: str, contexts: list[dict] | None = None) -> dict:
    return {"report": report, "retrieved_context_count": len(contexts or [])}

