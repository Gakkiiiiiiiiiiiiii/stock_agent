"""Content API compatibility facade backed exclusively by stock_content."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import dependencies
from app.routers._shared import _safe_api_limit, _validate_knowledge_filters
from financial_agent.models import ThemeLogic
from mcp_servers.knowledge_server import upsert_theme_logic as upsert_theme_logic_mcp

router = APIRouter()


class KnowledgeSearchRequest(BaseModel):
    query: str
    intent: str | None = None
    filters: dict | None = None
    limit: int = 20


class BilibiliIngestRequest(BaseModel):
    url: str | None = None
    bv_id: str | None = None
    force_reprocess: bool = False
    summary_mode: str = "investment"
    index_to_memory: bool = True
    use_diarization: bool = False
    language_hint: str | None = "zh"
    enable_visual_context: bool = True


class BilibiliSummarizeRequest(BilibiliIngestRequest):
    persist: bool = False


class XiaoeHlsIngestRequest(BaseModel):
    m3u8_url: str
    page_url: str | None = None
    title: str | None = None
    platform_video_id: str | None = None
    headers: dict[str, str] | None = None
    authorized_content: bool = False
    force_reprocess: bool = False
    summary_mode: str = "investment"
    index_to_memory: bool = True
    use_diarization: bool = False
    language_hint: str | None = "zh"
    enable_visual_context: bool = False


class XiaoeHlsSummarizeRequest(XiaoeHlsIngestRequest):
    persist: bool = False


def _bilibili_options(request: BilibiliIngestRequest) -> dict:
    return request.model_dump(exclude={"url", "bv_id"})


@router.post("/api/v1/content/bilibili/ingest")
def ingest_bilibili_video(request: BilibiliIngestRequest) -> dict:
    if not request.url and not request.bv_id:
        raise HTTPException(status_code=400, detail="url or bv_id is required")
    return dependencies.content_ingest_service.enqueue_bilibili(
        url=request.url, bv_id=request.bv_id, **_bilibili_options(request)
    )


@router.post("/api/v1/content/bilibili/summarize")
def summarize_bilibili_video(request: BilibiliSummarizeRequest) -> dict:
    queued = ingest_bilibili_video(BilibiliIngestRequest(**request.model_dump(exclude={"persist"})))
    return {"task": queued, "status": queued.get("status", "PENDING"), "worker_owned": True}


@router.post("/api/v1/content/xiaoe/hls/ingest")
def ingest_xiaoe_hls_video(request: XiaoeHlsIngestRequest) -> dict:
    if not request.authorized_content:
        raise HTTPException(status_code=400, detail="authorized_content=true is required")
    return dependencies.content_ingest_service.enqueue_xiaoe_hls(**request.model_dump())


@router.post("/api/v1/content/xiaoe/hls/summarize")
def summarize_xiaoe_hls_video(request: XiaoeHlsSummarizeRequest) -> dict:
    queued = ingest_xiaoe_hls_video(XiaoeHlsIngestRequest(**request.model_dump(exclude={"persist"})))
    return {"task": queued, "status": queued.get("status", "PENDING"), "worker_owned": True}


@router.get("/api/v1/content/tasks/{task_id}")
def get_content_task(task_id: str) -> dict:
    payload = dependencies.content_ingest_service.get_task(task_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="task not found")
    return payload


@router.post("/api/v1/content/tasks/{task_id}/process")
def process_content_task(task_id: str) -> dict:
    return {"started": False, "task": get_content_task(task_id), "worker_owned": True}


@router.get("/api/v1/content/videos")
def list_content_videos(summary_mode: str = "investment", limit: int = 50) -> dict:
    safe_limit, warnings = _safe_api_limit(limit, default=50)
    return {
        "items": dependencies.content_ingest_service.list_videos(limit=safe_limit),
        "limit": safe_limit,
        "next_cursor": None,
        "filters": {"summary_mode": summary_mode},
        "warnings": warnings,
    }


@router.get("/api/v1/content/videos/{video_id}")
def get_content_video(video_id: str, summary_mode: str = "investment") -> dict:
    payload = dependencies.content_ingest_service.get_video_detail(video_id, summary_mode=summary_mode)
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    return payload


@router.get("/api/v1/content/videos/{video_id}/summary-document")
def get_content_video_summary_document(video_id: str) -> dict:
    payload = dependencies.content_ingest_service.get_video_summary(video_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="summary not found")
    return payload


@router.get("/api/v1/content/videos/{video_id}/segments")
def get_content_video_segments(video_id: str) -> dict:
    payload = dependencies.content_ingest_service.get_video_segments(video_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    return payload


@router.get("/api/v1/content/videos/{video_id}/chapters")
def get_content_video_chapters(video_id: str, limit: int = 200) -> dict:
    safe_limit, warnings = _safe_api_limit(limit, default=200)
    payload = dependencies.content_ingest_service.get_video_chapters(video_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    items = payload.get("items", [])[:safe_limit]
    return {**payload, "chapters": items, "items": items, "limit": safe_limit, "warnings": warnings}


@router.get("/api/v1/content/videos/{video_id}/knowledge")
@router.get("/api/v1/content/videos/{video_id}/knowledge-units")
def list_content_video_knowledge(video_id: str, limit: int = 200) -> dict:
    payload = dependencies.content_ingest_service.list_video_knowledge_units(video_id, limit=limit)
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    return payload


@router.post("/api/v1/content/knowledge/search")
def search_content_video_knowledge(request: KnowledgeSearchRequest) -> dict:
    return dependencies.content_ingest_service.search_video_knowledge(
        request.query,
        filters=_validate_knowledge_filters(request.filters or {}),
        limit=request.limit,
        intent=request.intent,
    )


@router.get("/api/v1/content/knowledge/{unit_id}")
@router.get("/api/v1/content/knowledge-units/{unit_id}")
def get_content_knowledge_unit(unit_id: str) -> dict:
    payload = dependencies.content_ingest_service.get_knowledge_unit(unit_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="knowledge unit not found")
    return payload


@router.post("/api/v1/knowledge/theme")
def upsert_theme(theme: ThemeLogic) -> dict:
    return upsert_theme_logic_mcp(theme.model_dump())
