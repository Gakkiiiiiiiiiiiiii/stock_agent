"""Read-only content evidence API backed by stock_content."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import dependencies
from app.routers._shared import _safe_api_limit, _validate_knowledge_filters

router = APIRouter()


class KnowledgeSearchRequest(BaseModel):
    query: str
    intent: str | None = None
    filters: dict | None = None
    limit: int = 20


@router.get("/api/v1/content/videos")
def list_content_videos(summary_mode: str = "investment", limit: int = 50) -> dict:
    safe_limit, warnings = _safe_api_limit(limit, default=50)
    return {"items": dependencies.content_client.list_videos(limit=safe_limit), "limit": safe_limit, "next_cursor": None, "filters": {"summary_mode": summary_mode}, "warnings": warnings}


@router.get("/api/v1/content/videos/{video_id}")
def get_content_video(video_id: str, summary_mode: str = "investment") -> dict:
    payload = dependencies.content_client.get_video_detail(video_id, summary_mode=summary_mode)
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    return payload


@router.get("/api/v1/content/videos/{video_id}/summary-document")
def get_content_video_summary_document(video_id: str) -> dict:
    payload = dependencies.content_client.get_video_summary(video_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="summary not found")
    return payload


@router.get("/api/v1/content/videos/{video_id}/segments")
def get_content_video_segments(video_id: str) -> dict:
    payload = dependencies.content_client.get_video_segments(video_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    return payload


@router.get("/api/v1/content/videos/{video_id}/chapters")
def get_content_video_chapters(video_id: str, limit: int = 200) -> dict:
    safe_limit, warnings = _safe_api_limit(limit, default=200)
    payload = dependencies.content_client.get_video_chapters(video_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    items = payload.get("items", [])[:safe_limit]
    return {**payload, "chapters": items, "items": items, "limit": safe_limit, "warnings": warnings}


@router.get("/api/v1/content/videos/{video_id}/knowledge")
@router.get("/api/v1/content/videos/{video_id}/knowledge-units")
def list_content_video_knowledge(video_id: str, limit: int = 200) -> dict:
    payload = dependencies.content_client.list_video_knowledge_units(video_id, limit=limit)
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    return payload


@router.post("/api/v1/content/knowledge/search")
def search_content_video_knowledge(request: KnowledgeSearchRequest) -> dict:
    return dependencies.content_client.search_video_knowledge(request.query, filters=_validate_knowledge_filters(request.filters or {}), limit=request.limit, intent=request.intent)


@router.get("/api/v1/content/knowledge/{unit_id}")
@router.get("/api/v1/content/knowledge-units/{unit_id}")
def get_content_knowledge_unit(unit_id: str) -> dict:
    payload = dependencies.content_client.get_knowledge_unit(unit_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="knowledge unit not found")
    return payload
