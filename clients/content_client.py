from __future__ import annotations

from typing import Any, Protocol

from app.model_gateway.metrics import TraceContext
from clients._http import SubsystemHttpClient


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    if isinstance(value, dict):
        merged = dict(value)
        for key in ("contract_version", "service_version", "snapshot_id", "as_of", "available_at"):
            if key in payload:
                merged.setdefault(key, payload[key])
        return merged
    return payload


class ContentClient(Protocol):
    def get_video_detail(self, video_id: str, summary_mode: str = "investment") -> dict[str, Any] | None: ...
    def search_video_knowledge(self, query: str, *, filters: dict[str, Any] | None = None, limit: int = 20, intent: str | None = None) -> dict[str, Any]: ...


class RemoteContentClient(SubsystemHttpClient):
    def __init__(self, base_url: str | None = None, *, timeout_seconds: float = 30.0, retries: int = 2, **http_kwargs) -> None:
        import os
        super().__init__(base_url or os.getenv("CONTENT_SERVICE_URL", "http://stock-content:8100"), timeout_seconds=timeout_seconds, retries=retries, **http_kwargs)

    def get_video_detail(self, video_id: str, summary_mode: str = "investment", *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        return _data(self.request("GET", f"/api/v1/videos/{video_id}", trace=trace, trace_context=trace_context, contract_version="content.v1"))

    def get_video_segments(self, video_id: str, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        return self.request("GET", f"/api/v1/videos/{video_id}/segments", trace=trace, trace_context=trace_context)

    def get_video_chapters(self, video_id: str, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        return self.request("GET", f"/api/v1/videos/{video_id}/chapters", trace=trace, trace_context=trace_context)

    def get_video_summary(self, video_id: str, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        return _data(self.request("GET", f"/api/v1/videos/{video_id}/summary", trace=trace, trace_context=trace_context))

    def list_video_knowledge_units(self, video_id: str, *, limit: int = 200, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None, **_: Any) -> dict[str, Any] | None:
        return self.request("GET", f"/api/v1/videos/{video_id}/knowledge", params={"limit": limit}, trace=trace, trace_context=trace_context)

    def get_knowledge_unit(self, unit_id: str, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        return _data(self.request("GET", f"/api/v1/knowledge/{unit_id}", trace=trace, trace_context=trace_context))

    def list_videos(self, limit: int = 50, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return self.request("GET", "/api/v1/videos", params={"limit": limit}, trace=trace, trace_context=trace_context).get("items", [])

    def search_video_knowledge(self, query: str, *, filters: dict[str, Any] | None = None, limit: int = 20, intent: str | None = None, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("POST", "/api/v1/knowledge/search", payload={"query": query, "filters": filters or {}, "limit": limit, "intent": intent}, trace=trace, trace_context=trace_context, contract_version="content.v1")

    def __getattr__(self, name: str):
        raise AttributeError(f"content.v1 no longer exposes legacy operation: {name}")


def build_content_knowledge_bundle_client(*, base_url: str | None = None):
    """Keep conclusion's bundle boundary distinct from browse/Search content.v1."""
    from app.adapters.http.content_knowledge_client import (
        RemoteContentKnowledgeBundleClient,
    )

    return RemoteContentKnowledgeBundleClient(base_url)
