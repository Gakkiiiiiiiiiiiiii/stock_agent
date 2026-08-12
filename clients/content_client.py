from __future__ import annotations

from typing import Any, Protocol

from clients._http import SubsystemHttpClient
from contracts.content import ContentSignalRequest


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    return value if isinstance(value, dict) else payload


class ContentClient(Protocol):
    def enqueue_bilibili(self, **kwargs: Any) -> dict[str, Any]: ...
    def enqueue_xiaoe_hls(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_task(self, task_id: str) -> dict[str, Any] | None: ...
    def get_video_detail(self, video_id: str, summary_mode: str = "investment") -> dict[str, Any] | None: ...
    def search_video_knowledge(self, query: str, *, filters: dict[str, Any] | None = None, limit: int = 20, intent: str | None = None) -> dict[str, Any]: ...


class RemoteContentClient(SubsystemHttpClient):
    def enqueue_bilibili(self, **kwargs: Any) -> dict[str, Any]:
        options = {key: value for key, value in kwargs.items() if key not in {"url", "bv_id"} and value is not None}
        return _data(self.request("POST", "/api/v1/videos/bilibili/ingest", payload={"url": kwargs.get("url"), "bv_id": kwargs.get("bv_id"), "options": options}))

    def enqueue_xiaoe_hls(self, **kwargs: Any) -> dict[str, Any]:
        options = {key: value for key, value in kwargs.items() if key != "m3u8_url" and value is not None}
        return _data(self.request("POST", "/api/v1/videos/xiaoe/ingest", payload={"m3u8_url": kwargs.get("m3u8_url"), "options": options}))

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return _data(self.request("GET", f"/api/v1/tasks/{task_id}"))

    def get_video_detail(self, video_id: str, summary_mode: str = "investment") -> dict[str, Any] | None:
        return _data(self.request("GET", f"/api/v1/videos/{video_id}"))

    def get_video_segments(self, video_id: str) -> dict[str, Any] | None:
        return self.request("GET", f"/api/v1/videos/{video_id}/segments")

    def get_video_chapters(self, video_id: str) -> dict[str, Any] | None:
        return self.request("GET", f"/api/v1/videos/{video_id}/chapters")

    def get_video_summary(self, video_id: str) -> dict[str, Any] | None:
        return _data(self.request("GET", f"/api/v1/videos/{video_id}/summary"))

    def list_video_knowledge_units(self, video_id: str, *, limit: int = 200, **_: Any) -> dict[str, Any] | None:
        return self.request("GET", f"/api/v1/videos/{video_id}/knowledge", params={"limit": limit})

    def get_knowledge_unit(self, unit_id: str) -> dict[str, Any] | None:
        return _data(self.request("GET", f"/api/v1/knowledge/{unit_id}"))

    def list_videos(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.request("GET", "/api/v1/videos", params={"limit": limit}).get("items", [])

    def search_video_knowledge(self, query: str, *, filters: dict[str, Any] | None = None, limit: int = 20, intent: str | None = None) -> dict[str, Any]:
        return self.request("POST", "/api/v1/knowledge/search", payload={"query": query, "filters": filters or {}, "limit": limit, "intent": intent})

    def content_factor_signals(self, request: ContentSignalRequest) -> dict[str, Any]:
        return self.request("POST", "/internal/v1/factor-signals", payload=request.model_dump())

    def __getattr__(self, name: str):
        raise AttributeError(f"content.v1 no longer exposes legacy operation: {name}")
