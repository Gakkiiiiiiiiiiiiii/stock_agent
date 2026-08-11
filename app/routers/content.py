"""内容摄取与视频知识路由（从 app/api.py 平移，路由契约不变）。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import dependencies
from app.routers._shared import (
    VALID_KNOWLEDGE_KINDS,
    VALID_LIFECYCLE_STATUSES,
    VALID_TEMPORAL_CLASSES,
    VALID_VERIFICATION_STATUSES,
    _normalize_enum_param,
    _safe_api_limit,
    _validate_knowledge_filters,
)
from financial_agent.models import ThemeLogic
from mcp_servers.knowledge_server import upsert_theme_logic as upsert_theme_logic_mcp
from storage.repositories.job_repository import JobTaskRepository
from workers.job_types import JobType

router = APIRouter()


class KnowledgeSearchRequest(BaseModel):
    query: str
    intent: str | None = None
    filters: dict | None = None
    limit: int = 20


class KnowledgeLifecycleUpdateRequest(BaseModel):
    lifecycle_status: str | None = None
    verification_status: str | None = None  # deprecated 兼容字段，见 knowledge_enums.VerificationStatus
    review_status: str | None = None
    valid_to: datetime | None = None
    note: str | None = None
    operator: str | None = None


class KnowledgeLifecycleSweepRequest(BaseModel):
    now: datetime | None = None
    limit: int = 500
    idempotency_key: str | None = None


class VideoReparseRequest(BaseModel):
    index_knowledge: bool = True


class BilibiliIngestRequest(BaseModel):
    url: str | None = None
    bv_id: str | None = None
    force_reprocess: bool = False
    summary_mode: str = "investment"
    index_to_memory: bool = True
    use_diarization: bool = False
    language_hint: str | None = "zh"
    enable_visual_context: bool = True


class BilibiliSummarizeRequest(BaseModel):
    url: str | None = None
    bv_id: str | None = None
    persist: bool = False
    summary_mode: str = "investment"
    index_to_memory: bool = True
    use_diarization: bool = False
    language_hint: str | None = "zh"
    enable_visual_context: bool = True


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
    author_name: str | None = None
    publish_time: str | None = None
    duration_seconds: int | None = None
    cover_url: str | None = None
    description: str | None = None
    engine: str = "ffmpeg-direct"
    quality: str = "best"
    workers: int = 4
    timeout_seconds: int = 30


class XiaoeHlsSummarizeRequest(XiaoeHlsIngestRequest):
    persist: bool = False


@router.post("/api/v1/content/bilibili/ingest")
def ingest_bilibili_video(request: BilibiliIngestRequest) -> dict:
    if not request.url and not request.bv_id:
        raise HTTPException(status_code=400, detail="url or bv_id is required")
    return dependencies.content_ingest_service.enqueue_bilibili(
        url=request.url,
        bv_id=request.bv_id,
        force_reprocess=request.force_reprocess,
        summary_mode=request.summary_mode,
        index_to_memory=request.index_to_memory,
        use_diarization=request.use_diarization,
        language_hint=request.language_hint,
        enable_visual_context=request.enable_visual_context,
    )


@router.post("/api/v1/content/bilibili/summarize")
def summarize_bilibili_video(request: BilibiliSummarizeRequest) -> dict:
    if not request.url and not request.bv_id:
        raise HTTPException(status_code=400, detail="url or bv_id is required")
    queued = dependencies.content_ingest_service.enqueue_bilibili(
        url=request.url,
        bv_id=request.bv_id,
        force_reprocess=request.persist,
        summary_mode=request.summary_mode,
        index_to_memory=request.index_to_memory,
        use_diarization=request.use_diarization,
        language_hint=request.language_hint,
        enable_visual_context=request.enable_visual_context,
    )
    if queued.get("task_id") is None and queued.get("video_id") is not None:
        detail = dependencies.content_ingest_service.get_video_detail(queued["video_id"], summary_mode=request.summary_mode)
        return {"task": queued, **(detail or {})}
    detail = dependencies.content_ingest_service.process_task(queued["task_id"])
    return {"task": dependencies.content_ingest_service.get_task(queued["task_id"]), **detail}


@router.post("/api/v1/content/xiaoe/hls/ingest")
def ingest_xiaoe_hls_video(request: XiaoeHlsIngestRequest) -> dict:
    if not request.authorized_content:
        raise HTTPException(status_code=400, detail="authorized_content=true is required")
    try:
        return dependencies.content_ingest_service.enqueue_xiaoe_hls(
            m3u8_url=request.m3u8_url,
            page_url=request.page_url,
            title=request.title,
            platform_video_id=request.platform_video_id,
            headers=request.headers,
            authorized_content=request.authorized_content,
            force_reprocess=request.force_reprocess,
            summary_mode=request.summary_mode,
            index_to_memory=request.index_to_memory,
            use_diarization=request.use_diarization,
            language_hint=request.language_hint,
            enable_visual_context=request.enable_visual_context,
            author_name=request.author_name,
            publish_time=request.publish_time,
            duration_seconds=request.duration_seconds,
            cover_url=request.cover_url,
            description=request.description,
            engine=request.engine,
            quality=request.quality,
            workers=request.workers,
            timeout_seconds=request.timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/v1/content/xiaoe/hls/summarize")
def summarize_xiaoe_hls_video(request: XiaoeHlsSummarizeRequest) -> dict:
    if not request.authorized_content:
        raise HTTPException(status_code=400, detail="authorized_content=true is required")
    try:
        queued = dependencies.content_ingest_service.enqueue_xiaoe_hls(
            m3u8_url=request.m3u8_url,
            page_url=request.page_url,
            title=request.title,
            platform_video_id=request.platform_video_id,
            headers=request.headers,
            authorized_content=request.authorized_content,
            force_reprocess=request.persist,
            summary_mode=request.summary_mode,
            index_to_memory=request.index_to_memory,
            use_diarization=request.use_diarization,
            language_hint=request.language_hint,
            enable_visual_context=request.enable_visual_context,
            author_name=request.author_name,
            publish_time=request.publish_time,
            duration_seconds=request.duration_seconds,
            cover_url=request.cover_url,
            description=request.description,
            engine=request.engine,
            quality=request.quality,
            workers=request.workers,
            timeout_seconds=request.timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if queued.get("task_id") is None and queued.get("video_id") is not None:
        detail = dependencies.content_ingest_service.get_video_detail(queued["video_id"], summary_mode=request.summary_mode)
        return {"task": queued, **(detail or {})}
    detail = dependencies.content_ingest_service.process_task(queued["task_id"])
    return {"task": dependencies.content_ingest_service.get_task(queued["task_id"]), **detail}


@router.get("/api/v1/content/tasks/{task_id}")
def get_content_task(task_id: int) -> dict:
    task = dependencies.content_ingest_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.post("/api/v1/content/tasks/{task_id}/process")
def process_content_task(task_id: int, background_tasks: BackgroundTasks) -> dict:
    task = dependencies.content_ingest_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task.get("status") in {"processing", "success"}:
        return {"started": False, "task": task}
    background_tasks.add_task(dependencies.content_ingest_service.process_task, task_id)
    return {"started": True, "task": task}


@router.get("/api/v1/content/videos")
def list_content_videos(summary_mode: str = "investment", limit: int = 50) -> dict:
    safe_limit, warnings = _safe_api_limit(limit, default=50)
    return {
        "items": dependencies.content_ingest_service.list_videos(summary_mode=summary_mode, limit=safe_limit),
        "limit": safe_limit,
        "next_cursor": None,
        "filters": {"summary_mode": summary_mode},
        "warnings": warnings,
    }


@router.get("/api/v1/content/videos/{video_id}")
def get_content_video(video_id: int, summary_mode: str = "investment") -> dict:
    detail = dependencies.content_ingest_service.get_video_detail(video_id, summary_mode=summary_mode)
    if detail is None:
        raise HTTPException(status_code=404, detail="video not found")
    return detail


@router.get("/api/v1/content/videos/{video_id}/summary-document")
def get_content_video_summary_document(video_id: int, summary_mode: str = "investment") -> dict:
    payload = dependencies.content_ingest_service.get_video_summary_document(video_id, summary_mode=summary_mode)
    if payload is None:
        raise HTTPException(status_code=404, detail="summary document not found")
    return payload


@router.delete("/api/v1/content/videos/{video_id}/summary")
def delete_content_video_summary(video_id: int, summary_mode: str = "investment") -> dict:
    payload = dependencies.content_ingest_service.delete_video_summary(video_id, summary_mode=summary_mode)
    if payload is None:
        raise HTTPException(status_code=404, detail="summary not found")
    return payload


@router.get("/api/v1/content/videos/{video_id}/segments")
def get_content_video_segments(video_id: int) -> dict:
    payload = dependencies.content_ingest_service.get_video_segments(video_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    return payload


@router.get("/api/v1/content/videos/{video_id}/events")
def get_content_video_events(video_id: int, summary_mode: str = "investment") -> dict:
    payload = dependencies.content_ingest_service.get_video_events(video_id, summary_mode=summary_mode)
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    return payload


@router.get("/api/v1/content/videos/{video_id}/chapters")
def get_content_video_chapters(video_id: int, limit: int = 200) -> dict:
    payload = dependencies.content_ingest_service.get_video_chapters(video_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    safe_limit, warnings = _safe_api_limit(limit, default=200)
    items = (payload.get("chapters") or [])[:safe_limit]
    return payload | {"chapters": items, "items": items, "limit": safe_limit, "next_cursor": None, "filters": {}, "warnings": warnings}


@router.get("/api/v1/content/videos/{video_id}/chapters/{chapter_id}")
def get_content_video_chapter(video_id: int, chapter_id: int) -> dict:
    payload = dependencies.content_ingest_service.get_video_chapter(video_id, chapter_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    return payload


@router.get("/api/v1/content/videos/{video_id}/knowledge")
def list_content_video_knowledge(
    video_id: int,
    primary_domain: str | None = None,
    knowledge_kind: str | None = None,
    temporal_class: str | None = None,
    lifecycle_status: str | None = None,
    verification_status: str | None = None,
    subject_key: str | None = None,
    valid_only: bool = False,
    limit: int = 200,
) -> dict:
    knowledge_kind = _normalize_enum_param(knowledge_kind, VALID_KNOWLEDGE_KINDS, "knowledge_kind")
    temporal_class = _normalize_enum_param(temporal_class, VALID_TEMPORAL_CLASSES, "temporal_class")
    lifecycle_status = _normalize_enum_param(lifecycle_status, VALID_LIFECYCLE_STATUSES, "lifecycle_status")
    verification_status = _normalize_enum_param(verification_status, VALID_VERIFICATION_STATUSES, "verification_status")
    filters = {
        key: value
        for key, value in {
            "primary_domain": primary_domain,
            "knowledge_kind": knowledge_kind,
            "temporal_class": temporal_class,
            "lifecycle_status": lifecycle_status,
            "verification_status": verification_status,
            "subject_key": subject_key,
            "valid_only": valid_only,
        }.items()
        if value not in (None, "", False)
    }
    payload = dependencies.content_ingest_service.list_video_knowledge_units(video_id, filters=filters, limit=limit)
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    return payload


@router.get("/api/v1/content/videos/{video_id}/knowledge-units")
def list_content_video_knowledge_alias(
    video_id: int,
    primary_domain: str | None = None,
    knowledge_kind: str | None = None,
    temporal_class: str | None = None,
    lifecycle_status: str | None = None,
    verification_status: str | None = None,
    subject_key: str | None = None,
    valid_only: bool = False,
    limit: int = 200,
) -> dict:
    return list_content_video_knowledge(
        video_id,
        primary_domain=primary_domain,
        knowledge_kind=knowledge_kind,
        temporal_class=temporal_class,
        lifecycle_status=lifecycle_status,
        verification_status=verification_status,
        subject_key=subject_key,
        valid_only=valid_only,
        limit=limit,
    )


@router.post("/api/v1/content/knowledge/search")
def search_content_video_knowledge(request: KnowledgeSearchRequest) -> dict:
    filters = _validate_knowledge_filters(request.filters or {})
    return dependencies.content_ingest_service.search_video_knowledge(request.query, filters=filters, limit=request.limit, intent=request.intent)


@router.get("/api/v1/content/knowledge/conflicts")
def list_content_knowledge_conflicts(subject_key: str | None = None, limit: int = 50) -> dict:
    return dependencies.content_ingest_service.list_knowledge_conflicts(subject_key=subject_key, limit=limit)


@router.get("/api/v1/content/knowledge/subjects/{subject_key}/current")
def get_content_subject_current_state(subject_key: str, domain: str | None = None, limit: int = 20) -> dict:
    payload = dependencies.content_ingest_service.get_current_subject_state(subject_key=subject_key, domain=domain, limit=limit)
    return payload | {"next_cursor": None, "filters": {"subject_key": subject_key, "domain": domain}, "warnings": payload.get("warnings") or []}


@router.get("/api/v1/content/knowledge/subjects/{subject_key}/history")
def get_content_subject_history(subject_key: str, domain: str | None = None, limit: int = 50) -> dict:
    payload = dependencies.content_ingest_service.get_subject_history(subject_key=subject_key, domain=domain, limit=limit)
    return payload | {"next_cursor": None, "filters": {"subject_key": subject_key, "domain": domain}, "warnings": payload.get("warnings") or []}


@router.post("/api/v1/content/knowledge/lifecycle/sweep")
def sweep_content_knowledge_lifecycle(request: KnowledgeLifecycleSweepRequest) -> dict:
    return dependencies.content_ingest_service.expire_due_knowledge_units(now=request.now, limit=request.limit)


@router.post("/api/v1/content/knowledge/lifecycle/sweep-task")
def create_content_knowledge_lifecycle_sweep_task(request: KnowledgeLifecycleSweepRequest) -> dict:
    payload = {"limit": request.limit}
    if request.now:
        payload["now"] = request.now.isoformat()
    return JobTaskRepository().create(
        JobType.KNOWLEDGE_LIFECYCLE_SWEEP,
        payload,
        idempotency_key=request.idempotency_key,
    )


@router.get("/api/v1/content/knowledge/{unit_id}")
def get_content_knowledge_unit(unit_id: int) -> dict:
    payload = dependencies.content_ingest_service.get_knowledge_unit(unit_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="knowledge unit not found")
    return payload


@router.get("/api/v1/content/knowledge-units/{unit_id}")
def get_content_knowledge_unit_alias(unit_id: int) -> dict:
    return get_content_knowledge_unit(unit_id)


@router.post("/api/v1/content/videos/{video_id}/reparse")
def reparse_content_video_knowledge(video_id: int, request: VideoReparseRequest) -> dict:
    try:
        payload = dependencies.content_ingest_service.reparse_video_knowledge(video_id, index_knowledge=request.index_knowledge)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    return payload


@router.get("/api/v1/content/knowledge/{unit_id}/verifications")
def list_content_knowledge_verifications(unit_id: int, limit: int = 100) -> dict:
    """Admin 只读：verification ledger（含 provenance），设计文档 §90 P2。"""
    if dependencies.content_ingest_service.get_knowledge_unit(unit_id) is None:
        raise HTTPException(status_code=404, detail="knowledge unit not found")
    safe_limit, warnings = _safe_api_limit(limit, default=100)
    return {
        "knowledge_unit_id": unit_id,
        "items": dependencies.knowledge_repository.list_verifications(unit_id, limit=safe_limit),
        "limit": safe_limit,
        "next_cursor": None,
        "warnings": warnings,
    }


@router.get("/api/v1/content/knowledge/{unit_id}/evidence")
def get_content_knowledge_evidence(unit_id: int) -> dict:
    """Admin 只读：raw evidence（raw_text/word_timestamps/correction_trace/bbox 等），§90 P2。"""
    payload = dependencies.content_ingest_service.get_knowledge_unit(unit_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="knowledge unit not found")
    return {"knowledge_unit_id": unit_id, "items": payload.get("evidence") or []}


@router.patch("/api/v1/content/knowledge/{unit_id}/lifecycle")
def update_content_knowledge_lifecycle(unit_id: int, request: KnowledgeLifecycleUpdateRequest) -> dict:
    payload = _update_content_knowledge_lifecycle(unit_id, request)
    if payload is None:
        raise HTTPException(status_code=404, detail="knowledge unit not found")
    return payload


@router.patch("/api/v1/content/knowledge-units/{unit_id}/lifecycle")
def update_content_knowledge_lifecycle_alias(unit_id: int, request: KnowledgeLifecycleUpdateRequest) -> dict:
    return update_content_knowledge_lifecycle(unit_id, request)


@router.get("/api/v1/content/knowledge-units/{unit_id}/lifecycle-audits")
def list_content_knowledge_lifecycle_audits(unit_id: int, limit: int = 50) -> dict:
    return dependencies.content_ingest_service.list_knowledge_unit_lifecycle_audits(unit_id, limit=limit)


def _update_content_knowledge_lifecycle(unit_id: int, request: KnowledgeLifecycleUpdateRequest) -> dict | None:
    try:
        return dependencies.content_ingest_service.update_knowledge_unit_lifecycle(
            unit_id,
            lifecycle_status=request.lifecycle_status,
            verification_status=request.verification_status,
            review_status=request.review_status,
            valid_to=request.valid_to,
            note=request.note,
            operator=request.operator,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/v1/content/videos/{video_id}/frames/{frame_index}/image")
def get_content_video_frame_image(video_id: int, frame_index: int) -> FileResponse:
    image_path = dependencies.content_ingest_service.get_video_frame_image_path(video_id, frame_index)
    if image_path is None:
        raise HTTPException(status_code=404, detail="frame not found")
    return FileResponse(image_path)


@router.get("/api/v1/content/video-frames/{bvid}/{filename}")
def get_content_video_frame_image_by_filename(bvid: str, filename: str) -> FileResponse:
    image_path = dependencies.content_ingest_service.get_video_frame_image_path_by_filename(bvid, filename)
    if image_path is None:
        raise HTTPException(status_code=404, detail="frame not found")
    return FileResponse(image_path)


@router.post("/api/v1/knowledge/theme")
def upsert_theme(theme: ThemeLogic) -> dict:
    return upsert_theme_logic_mcp(theme.model_dump())
