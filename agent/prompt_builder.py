from __future__ import annotations


def build_prompt(task_type: str, user_query: str, retrieved_context: list[dict] | None = None) -> str:
    context_lines = "\n".join(f"- {item.get('title')}: {item.get('content', '')[:120]}" for item in (retrieved_context or []))
    return f"Task type: {task_type}\nUser query: {user_query}\nRetrieved context:\n{context_lines}"

