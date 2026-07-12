from __future__ import annotations


def search_news(query: str, limit: int = 10) -> dict:
    return {"query": query, "items": [], "warning": "MVP 暂未接入新闻源"}


def search_announcements(symbol: str | None = None, query: str | None = None) -> dict:
    return {"symbol": symbol, "query": query, "items": [], "warning": "MVP 暂未接入公告源"}


def summarize_event_impact(title: str, content: str = "") -> dict:
    text = f"{title} {content}"
    return {"impact": "unknown", "summary": text[:200], "warning": "MVP 仅回传结构化占位结果"}

