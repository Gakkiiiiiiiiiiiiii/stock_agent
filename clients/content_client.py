"""Content service ports plus local and HTTP implementations.

No caller outside this module needs to import ``engines.content`` when the
remote backend is selected.
"""
from __future__ import annotations

from typing import Any, Protocol

from clients._http import SubsystemHttpClient
from contracts.content import ContentSignalRequest


class ContentClient(Protocol):
    def enqueue_bilibili(self, **kwargs: Any) -> dict[str, Any]: ...
    def enqueue_xiaoe_hls(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_video_detail(self, video_id: int, summary_mode: str = "investment") -> dict[str, Any] | None: ...
    def get_video_segments(self, video_id: int) -> dict[str, Any] | None: ...
    def search_video_knowledge(self, query: str, *, filters: dict[str, Any] | None = None, limit: int = 20, intent: str | None = None) -> dict[str, Any]: ...
    def content_factor_signals(self, request: ContentSignalRequest) -> dict[str, Any]: ...


class LocalContentClient:
    """Temporary compatibility adapter for the pre-split implementation."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service, name)

    def enqueue_bilibili(self, **kwargs: Any) -> dict[str, Any]:
        return self._service.enqueue_bilibili(**kwargs)

    def enqueue_xiaoe_hls(self, **kwargs: Any) -> dict[str, Any]:
        return self._service.enqueue_xiaoe_hls(**kwargs)

    def get_video_detail(self, video_id: int, summary_mode: str = "investment") -> dict[str, Any] | None:
        return self._service.get_video_detail(video_id, summary_mode=summary_mode)

    def get_video_segments(self, video_id: int) -> dict[str, Any] | None:
        return self._service.get_video_segments(video_id)

    def search_video_knowledge(self, query: str, *, filters: dict[str, Any] | None = None, limit: int = 20, intent: str | None = None) -> dict[str, Any]:
        return self._service.search_video_knowledge(query, filters=filters, limit=limit, intent=intent)

    def content_factor_signals(self, request: ContentSignalRequest) -> dict[str, Any]:
        return {"contract_version": "content-factor-signal.v1", "items": []}


class RemoteContentClient(SubsystemHttpClient):
    def enqueue_bilibili(self, **kwargs: Any) -> dict[str, Any]:
        return self.request("POST", "/api/v1/videos/bilibili/ingest", payload=kwargs)

    def enqueue_xiaoe_hls(self, **kwargs: Any) -> dict[str, Any]:
        return self.request("POST", "/api/v1/videos/xiaoe/ingest", payload=kwargs)

    def get_video_detail(self, video_id: int, summary_mode: str = "investment") -> dict[str, Any] | None:
        payload = self.request("GET", f"/api/v1/videos/{video_id}", params={"summary_mode": summary_mode})
        return payload if payload.get("found", True) else None

    def get_video_segments(self, video_id: int) -> dict[str, Any] | None:
        payload = self.request("GET", f"/api/v1/videos/{video_id}/segments")
        return payload if payload.get("found", True) else None

    def search_video_knowledge(self, query: str, *, filters: dict[str, Any] | None = None, limit: int = 20, intent: str | None = None) -> dict[str, Any]:
        return self.request("POST", "/api/v1/knowledge/search", payload={"query": query, "filters": filters or {}, "limit": limit, "intent": intent})

    def content_factor_signals(self, request: ContentSignalRequest) -> dict[str, Any]:
        return self.request("POST", "/internal/v1/factor-signals", payload=request.model_dump())
