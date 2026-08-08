from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date as Date
from datetime import datetime
from queue import Queue
from threading import Thread

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from app.admin_service import AdminContentService
from app.agent_orchestrator import AgentOrchestrator
from app.chat_history_service import ChatHistoryService
from app.dependencies import init_application
from app.security import render_metrics, security_and_trace_middleware
from engines.content.video_ingest_service import VideoIngestService
from engines.market.qmt_bridge_client import QmtBridgeClient
from engines.risk.portfolio_risk import evaluate_portfolio_risk
from financial_agent.models import Position, ThemeLogic, TradeReviewInput
from mcp_servers.knowledge_server import upsert_theme_logic as upsert_theme_logic_mcp
from sqlalchemy import text
from storage.db import session_scope
from storage.repositories.job_repository import JobTaskRepository

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_application()
    yield


app = FastAPI(title="Financial Analysis Agent", version="0.1.0", lifespan=lifespan)
app.middleware("http")(security_and_trace_middleware)
orchestrator = AgentOrchestrator()
admin_service = AdminContentService()
chat_history_service = ChatHistoryService()
content_ingest_service = VideoIngestService()

MAX_API_LIST_LIMIT = 200
VALID_KNOWLEDGE_KINDS = {
    "METHOD",
    "CONCEPT",
    "CAUSAL_THESIS",
    "FACT",
    "STATE",
    "FORECAST",
    "TECHNICAL_SIGNAL",
    "ACTION",
    "RISK_CONDITION",
    "MODEL_INFERENCE",
}
VALID_TEMPORAL_CLASSES = {"DURABLE", "CYCLICAL", "SNAPSHOT", "EVENT_BOUND"}
VALID_LIFECYCLE_STATUSES = {
    "EXTRACTED",
    "ACTIVE",
    "VALIDATED",
    "SUPERSEDED",
    "EXPIRED",
    "REJECTED",
    "RETIRED",
}
VALID_VERIFICATION_STATUSES = {
    "UNVERIFIED",
    "UNSUPPORTED",
    "SOURCE_LOCATED",
    "SOURCE_SUPPORTED",
    "CROSS_MODAL_SUPPORTED",
    "EXTERNALLY_VERIFIED",
    "SOURCE_CONFIRMED",
    "VERIFIED",
    "VALIDATED",
    "REJECTED",
    "NEEDS_REVIEW",
}


class StockAnalyzeRequest(BaseModel):
    symbol: str
    date: Date | None = None
    analysis_type: str = "full"
    patterns: list[str] | None = None


class ThemeAnalyzeRequest(BaseModel):
    theme_name: str
    date: Date | None = None


class DailyScanRequest(BaseModel):
    date: Date | None = None
    mode: str = "after_close"


class AgentRunRequest(BaseModel):
    query: str
    context: dict | None = None
    skill: str | None = None
    session_id: str | None = None


class RetrievalRequest(BaseModel):
    query: str
    task_type: str | None = None
    filters: dict | None = None
    top_k: int = 5


class MarketRegimeRequest(BaseModel):
    snapshot: dict | None = None
    as_of: datetime | None = None
    up_count: int | None = None
    down_count: int | None = None
    index_return_5d: float | None = None
    index_return_20d: float | None = None
    top_theme_strength: float | None = None
    limit_up_count: int | None = None
    index_volatility: float | None = None
    index_volatility_20d: float | None = None
    index_drawdown_20d: float | None = None
    limit_down_count: int | None = None
    previous_regime: str | None = None
    high_position_loss_ratio: float | None = None
    high_position_limit_down_ratio: float | None = None
    high_position_breakdown_ratio: float | None = None
    high_position_big_negative_count: int | None = None
    retreat_days: int | None = None
    force_refresh: bool = False


class KnowledgeDocUpdateRequest(BaseModel):
    path: str
    content: str


class KnowledgeSearchRequest(BaseModel):
    query: str
    filters: dict | None = None
    limit: int = 20


class KnowledgeLifecycleUpdateRequest(BaseModel):
    lifecycle_status: str | None = None
    verification_status: str | None = None
    valid_to: datetime | None = None
    note: str | None = None
    operator: str | None = None


class KnowledgeLifecycleSweepRequest(BaseModel):
    now: datetime | None = None
    limit: int = 500
    idempotency_key: str | None = None


class VideoReparseRequest(BaseModel):
    index_knowledge: bool = True


class ToolProposalRequest(BaseModel):
    tool_name: str
    payload: dict


class SkillUpdateRequest(BaseModel):
    slug: str
    name: str
    description: str = ""
    content: str


class AgentSessionCreateRequest(BaseModel):
    title: str | None = None


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


class FactorMineRequest(BaseModel):
    rounds: int | None = None
    candidates_per_round: int | None = None
    universe: list[str] | None = None
    days: int | None = None
    eval_window: int | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/health/live")
def health_live() -> dict:
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready(response: Response) -> dict:
    checks = _ready_checks()
    required = _required_ready_checks()
    ready = all(checks.get(name) == "ok" for name in required)
    if not ready:
        response.status_code = 503
    return {"status": "ok" if ready else "degraded", "checks": checks}


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    return PlainTextResponse(render_metrics(), media_type="text/plain")


def _ready_checks() -> dict[str, str]:
    required = _required_ready_checks()
    checks = {"api": "ok"}
    checks["postgres"] = _check_postgres()
    checks["qdrant"] = _check_http(f"{os.getenv('QDRANT_URL', 'http://localhost:6333').rstrip('/')}/collections", api_key=os.getenv("QDRANT_API_KEY"))
    checks["redis"] = _check_redis(os.getenv("REDIS_URL", ""))
    checks["embedding"] = _check_embedding()
    checks["reranker"] = _check_http(f"{os.getenv('RERANKER_URL', 'http://localhost:8010').rstrip('/')}/health")
    if "qmt" in required or os.getenv("READY_CHECK_OPTIONAL_QMT", "false").lower() in {"1", "true", "yes"}:
        checks["qmt"] = _check_qmt()
    else:
        checks["qmt"] = "skipped"
    return checks


def _required_ready_checks() -> set[str]:
    configured = os.getenv("READY_REQUIRED_CHECKS")
    if configured is None:
        required = {"api", "postgres", "qdrant", "embedding", "reranker"}
        if os.getenv("REDIS_URL"):
            required.add("redis")
        return required
    raw = configured
    return {item.strip() for item in raw.split(",") if item.strip()}


def _check_postgres() -> str:
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("ready check failed: postgres: %s", exc)
        return "failed"


def _check_http(url: str, api_key: str | None = None) -> str:
    headers = {"api-key": api_key} if api_key else None
    try:
        response = httpx.get(url, headers=headers, timeout=3)
        response.raise_for_status()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("ready check failed: http endpoint %s: %s", _redact_url(url), exc)
        return "failed"


def _check_embedding() -> str:
    base_url = os.getenv("EMBEDDING_BASE_URL", "http://localhost:8001/v1").rstrip("/")
    health_url = base_url[:-3] if base_url.endswith("/v1") else base_url
    return _check_http(f"{health_url}/health")


def _check_redis(redis_url: str) -> str:
    if not redis_url:
        return "skipped"
    try:
        import redis

        client = redis.Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
        return "ok" if client.ping() else "failed"
    except Exception as exc:  # noqa: BLE001
        logger.warning("ready check failed: redis: %s", exc)
        return "failed"


def _check_qmt() -> str:
    try:
        QmtBridgeClient().healthcheck()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("ready check failed: qmt: %s", exc)
        return "failed"


def _redact_url(url: str) -> str:
    try:
        parsed = httpx.URL(url)
        return str(parsed.copy_with(password="***") if parsed.password else parsed)
    except Exception:
        return "<invalid-url>"


@app.get("/admin")
def admin_console() -> FileResponse:
    return FileResponse(
        admin_service.root / "app" / "static" / "admin.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.post("/api/v1/analyze/stock")
def analyze_stock(request: StockAnalyzeRequest) -> dict:
    return orchestrator.analyze_stock(request.symbol, as_of=request.date, patterns=request.patterns)


@app.post("/api/v1/analyze/theme")
def analyze_theme(request: ThemeAnalyzeRequest) -> dict:
    return orchestrator.analyze_theme(request.theme_name)


@app.post("/api/v1/market/daily-scan")
def daily_scan(request: DailyScanRequest) -> dict:
    return orchestrator.daily_scan(scan_date=request.date, mode=request.mode)


@app.post("/api/v1/agent/run")
def run_agent(request: AgentRunRequest) -> dict:
    session = chat_history_service.ensure_session(request.session_id, title_hint=request.query)
    payload = orchestrator.run_agent(query=request.query, context=request.context, skill=request.skill)
    chat_history_service.save_turn(
        session_id=session["session_id"],
        user_query=request.query,
        assistant_content=payload.get("report") or payload.get("warning") or "",
        response=payload,
    )
    payload["session_id"] = session["session_id"]
    return payload


@app.post("/api/v1/agent/run/stream")
def run_agent_stream(request: AgentRunRequest) -> StreamingResponse:
    event_queue: Queue[tuple[str, dict] | None] = Queue()
    session = chat_history_service.ensure_session(request.session_id, title_hint=request.query)

    def emit(event: str, payload: dict) -> None:
        event_queue.put((event, payload))

    def worker() -> None:
        try:
            emit("session", {"session_id": session["session_id"], "title": session.get("title")})
            payload = orchestrator.run_agent(query=request.query, context=request.context, skill=request.skill, emit=emit)
            chat_history_service.save_turn(
                session_id=session["session_id"],
                user_query=request.query,
                assistant_content=payload.get("report") or payload.get("warning") or "",
                response={**payload, "session_id": session["session_id"]},
            )
        except Exception as exc:
            emit("error", {"message": str(exc)})
        finally:
            event_queue.put(None)

    def stream():
        while True:
            item = event_queue.get()
            if item is None:
                break
            event, payload = item
            yield _format_sse(event, payload)

    Thread(target=worker, daemon=True).start()
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/v1/agent/sessions")
def list_agent_sessions() -> dict:
    return {"items": chat_history_service.list_sessions()}


@app.post("/api/v1/agent/sessions")
def create_agent_session(request: AgentSessionCreateRequest) -> dict:
    return chat_history_service.create_session(title=request.title)


@app.get("/api/v1/agent/sessions/{session_id}")
def get_agent_session(session_id: str) -> dict:
    try:
        return chat_history_service.get_session(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"session not found: {exc}") from exc


@app.delete("/api/v1/agent/sessions/{session_id}")
def delete_agent_session(session_id: str) -> dict:
    try:
        chat_history_service.delete_session(session_id)
        return {"deleted": True, "session_id": session_id}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"session not found: {exc}") from exc


@app.post("/api/v1/retrieval/context")
def retrieve_context(request: RetrievalRequest) -> dict:
    from mcp_servers.retrieval_server import retrieve_relevant_context

    return retrieve_relevant_context(query=request.query, task_type=request.task_type, filters=request.filters, top_k=request.top_k)


@app.post("/api/v1/content/bilibili/ingest")
def ingest_bilibili_video(request: BilibiliIngestRequest) -> dict:
    if not request.url and not request.bv_id:
        raise HTTPException(status_code=400, detail="url or bv_id is required")
    return content_ingest_service.enqueue_bilibili(
        url=request.url,
        bv_id=request.bv_id,
        force_reprocess=request.force_reprocess,
        summary_mode=request.summary_mode,
        index_to_memory=request.index_to_memory,
        use_diarization=request.use_diarization,
        language_hint=request.language_hint,
        enable_visual_context=request.enable_visual_context,
    )


@app.post("/api/v1/content/bilibili/summarize")
def summarize_bilibili_video(request: BilibiliSummarizeRequest) -> dict:
    if not request.url and not request.bv_id:
        raise HTTPException(status_code=400, detail="url or bv_id is required")
    queued = content_ingest_service.enqueue_bilibili(
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
        detail = content_ingest_service.get_video_detail(queued["video_id"], summary_mode=request.summary_mode)
        return {"task": queued, **(detail or {})}
    detail = content_ingest_service.process_task(queued["task_id"])
    return {"task": content_ingest_service.get_task(queued["task_id"]), **detail}


@app.post("/api/v1/content/xiaoe/hls/ingest")
def ingest_xiaoe_hls_video(request: XiaoeHlsIngestRequest) -> dict:
    if not request.authorized_content:
        raise HTTPException(status_code=400, detail="authorized_content=true is required")
    try:
        return content_ingest_service.enqueue_xiaoe_hls(
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


@app.post("/api/v1/content/xiaoe/hls/summarize")
def summarize_xiaoe_hls_video(request: XiaoeHlsSummarizeRequest) -> dict:
    if not request.authorized_content:
        raise HTTPException(status_code=400, detail="authorized_content=true is required")
    try:
        queued = content_ingest_service.enqueue_xiaoe_hls(
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
        detail = content_ingest_service.get_video_detail(queued["video_id"], summary_mode=request.summary_mode)
        return {"task": queued, **(detail or {})}
    detail = content_ingest_service.process_task(queued["task_id"])
    return {"task": content_ingest_service.get_task(queued["task_id"]), **detail}


@app.get("/api/v1/content/tasks/{task_id}")
def get_content_task(task_id: int) -> dict:
    task = content_ingest_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@app.post("/api/v1/content/tasks/{task_id}/process")
def process_content_task(task_id: int, background_tasks: BackgroundTasks) -> dict:
    task = content_ingest_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task.get("status") in {"processing", "success"}:
        return {"started": False, "task": task}
    background_tasks.add_task(content_ingest_service.process_task, task_id)
    return {"started": True, "task": task}


@app.get("/api/v1/content/videos")
def list_content_videos(summary_mode: str = "investment", limit: int = 50) -> dict:
    safe_limit, warnings = _safe_api_limit(limit, default=50)
    return {
        "items": content_ingest_service.list_videos(summary_mode=summary_mode, limit=safe_limit),
        "limit": safe_limit,
        "next_cursor": None,
        "filters": {"summary_mode": summary_mode},
        "warnings": warnings,
    }


@app.get("/api/v1/content/videos/{video_id}")
def get_content_video(video_id: int, summary_mode: str = "investment") -> dict:
    detail = content_ingest_service.get_video_detail(video_id, summary_mode=summary_mode)
    if detail is None:
        raise HTTPException(status_code=404, detail="video not found")
    return detail


@app.get("/api/v1/content/videos/{video_id}/summary-document")
def get_content_video_summary_document(video_id: int, summary_mode: str = "investment") -> dict:
    payload = content_ingest_service.get_video_summary_document(video_id, summary_mode=summary_mode)
    if payload is None:
        raise HTTPException(status_code=404, detail="summary document not found")
    return payload


@app.delete("/api/v1/content/videos/{video_id}/summary")
def delete_content_video_summary(video_id: int, summary_mode: str = "investment") -> dict:
    payload = content_ingest_service.delete_video_summary(video_id, summary_mode=summary_mode)
    if payload is None:
        raise HTTPException(status_code=404, detail="summary not found")
    return payload


@app.get("/api/v1/content/videos/{video_id}/segments")
def get_content_video_segments(video_id: int) -> dict:
    payload = content_ingest_service.get_video_segments(video_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    return payload


@app.get("/api/v1/content/videos/{video_id}/events")
def get_content_video_events(video_id: int, summary_mode: str = "investment") -> dict:
    payload = content_ingest_service.get_video_events(video_id, summary_mode=summary_mode)
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    return payload


@app.get("/api/v1/content/videos/{video_id}/chapters")
def get_content_video_chapters(video_id: int, limit: int = 200) -> dict:
    payload = content_ingest_service.get_video_chapters(video_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    safe_limit, warnings = _safe_api_limit(limit, default=200)
    items = (payload.get("chapters") or [])[:safe_limit]
    return payload | {"chapters": items, "items": items, "limit": safe_limit, "next_cursor": None, "filters": {}, "warnings": warnings}


@app.get("/api/v1/content/videos/{video_id}/chapters/{chapter_id}")
def get_content_video_chapter(video_id: int, chapter_id: int) -> dict:
    payload = content_ingest_service.get_video_chapter(video_id, chapter_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    return payload


@app.get("/api/v1/content/videos/{video_id}/knowledge")
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
    payload = content_ingest_service.list_video_knowledge_units(video_id, filters=filters, limit=limit)
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    return payload


@app.get("/api/v1/content/videos/{video_id}/knowledge-units")
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


@app.post("/api/v1/content/knowledge/search")
def search_content_video_knowledge(request: KnowledgeSearchRequest) -> dict:
    filters = _validate_knowledge_filters(request.filters or {})
    return content_ingest_service.search_video_knowledge(request.query, filters=filters, limit=request.limit)


@app.get("/api/v1/content/knowledge/conflicts")
def list_content_knowledge_conflicts(subject_key: str | None = None, limit: int = 50) -> dict:
    return content_ingest_service.list_knowledge_conflicts(subject_key=subject_key, limit=limit)


@app.get("/api/v1/content/knowledge/subjects/{subject_key}/current")
def get_content_subject_current_state(subject_key: str, domain: str | None = None, limit: int = 20) -> dict:
    payload = content_ingest_service.get_current_subject_state(subject_key=subject_key, domain=domain, limit=limit)
    return payload | {"next_cursor": None, "filters": {"subject_key": subject_key, "domain": domain}, "warnings": payload.get("warnings") or []}


@app.get("/api/v1/content/knowledge/subjects/{subject_key}/history")
def get_content_subject_history(subject_key: str, domain: str | None = None, limit: int = 50) -> dict:
    payload = content_ingest_service.get_subject_history(subject_key=subject_key, domain=domain, limit=limit)
    return payload | {"next_cursor": None, "filters": {"subject_key": subject_key, "domain": domain}, "warnings": payload.get("warnings") or []}


@app.post("/api/v1/content/knowledge/lifecycle/sweep")
def sweep_content_knowledge_lifecycle(request: KnowledgeLifecycleSweepRequest) -> dict:
    return content_ingest_service.expire_due_knowledge_units(now=request.now, limit=request.limit)


@app.post("/api/v1/content/knowledge/lifecycle/sweep-task")
def create_content_knowledge_lifecycle_sweep_task(request: KnowledgeLifecycleSweepRequest) -> dict:
    payload = {"limit": request.limit}
    if request.now:
        payload["now"] = request.now.isoformat()
    return JobTaskRepository().create(
        "knowledge_lifecycle_sweep",
        payload,
        idempotency_key=request.idempotency_key,
    )


@app.get("/api/v1/content/knowledge/{unit_id}")
def get_content_knowledge_unit(unit_id: int) -> dict:
    payload = content_ingest_service.get_knowledge_unit(unit_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="knowledge unit not found")
    return payload


@app.get("/api/v1/content/knowledge-units/{unit_id}")
def get_content_knowledge_unit_alias(unit_id: int) -> dict:
    return get_content_knowledge_unit(unit_id)


@app.post("/api/v1/content/videos/{video_id}/reparse")
def reparse_content_video_knowledge(video_id: int, request: VideoReparseRequest) -> dict:
    try:
        payload = content_ingest_service.reparse_video_knowledge(video_id, index_knowledge=request.index_knowledge)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="video not found")
    return payload


@app.patch("/api/v1/content/knowledge/{unit_id}/lifecycle")
def update_content_knowledge_lifecycle(unit_id: int, request: KnowledgeLifecycleUpdateRequest) -> dict:
    payload = _update_content_knowledge_lifecycle(unit_id, request)
    if payload is None:
        raise HTTPException(status_code=404, detail="knowledge unit not found")
    return payload


@app.patch("/api/v1/content/knowledge-units/{unit_id}/lifecycle")
def update_content_knowledge_lifecycle_alias(unit_id: int, request: KnowledgeLifecycleUpdateRequest) -> dict:
    return update_content_knowledge_lifecycle(unit_id, request)


@app.get("/api/v1/content/knowledge-units/{unit_id}/lifecycle-audits")
def list_content_knowledge_lifecycle_audits(unit_id: int, limit: int = 50) -> dict:
    return content_ingest_service.list_knowledge_unit_lifecycle_audits(unit_id, limit=limit)


def _update_content_knowledge_lifecycle(unit_id: int, request: KnowledgeLifecycleUpdateRequest) -> dict | None:
    try:
        return content_ingest_service.update_knowledge_unit_lifecycle(
            unit_id,
            lifecycle_status=request.lifecycle_status,
            verification_status=request.verification_status,
            valid_to=request.valid_to,
            note=request.note,
            operator=request.operator,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _safe_api_limit(value: int | None, *, default: int, maximum: int = MAX_API_LIST_LIMIT) -> tuple[int, list[str]]:
    warnings: list[str] = []
    try:
        limit = int(value if value is not None else default)
    except (TypeError, ValueError):
        limit = default
        warnings.append("invalid_limit_defaulted")
    if limit <= 0:
        limit = default
        warnings.append("non_positive_limit_defaulted")
    if limit > maximum:
        limit = maximum
        warnings.append(f"limit_clamped_to_{maximum}")
    return limit, warnings


def _normalize_enum_param(value: str | None, allowed: set[str], field: str) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).upper()
    if normalized not in allowed:
        raise HTTPException(status_code=400, detail=f"invalid {field}: {value}")
    return normalized


def _validate_knowledge_filters(filters: dict) -> dict:
    validated = dict(filters or {})
    enum_fields = {
        "knowledge_kind": VALID_KNOWLEDGE_KINDS,
        "temporal_class": VALID_TEMPORAL_CLASSES,
        "lifecycle_status": VALID_LIFECYCLE_STATUSES,
        "verification_status": VALID_VERIFICATION_STATUSES,
    }
    for field, allowed in enum_fields.items():
        value = validated.get(field)
        if isinstance(value, list):
            validated[field] = [_normalize_enum_param(item, allowed, field) for item in value if item not in (None, "")]
        else:
            normalized = _normalize_enum_param(value, allowed, field)
            if normalized is None:
                validated.pop(field, None)
            else:
                validated[field] = normalized
    return validated


@app.get("/api/v1/content/videos/{video_id}/frames/{frame_index}/image")
def get_content_video_frame_image(video_id: int, frame_index: int) -> FileResponse:
    image_path = content_ingest_service.get_video_frame_image_path(video_id, frame_index)
    if image_path is None:
        raise HTTPException(status_code=404, detail="frame not found")
    return FileResponse(image_path)


@app.get("/api/v1/content/video-frames/{bvid}/{filename}")
def get_content_video_frame_image_by_filename(bvid: str, filename: str) -> FileResponse:
    image_path = content_ingest_service.get_video_frame_image_path_by_filename(bvid, filename)
    if image_path is None:
        raise HTTPException(status_code=404, detail="frame not found")
    return FileResponse(image_path)


@app.post("/api/v1/market/regime")
def market_regime(request: MarketRegimeRequest) -> dict:
    from mcp_servers.market_regime_server import get_market_regime

    return get_market_regime(**request.model_dump())


@app.post("/api/v1/knowledge/theme")
def upsert_theme(theme: ThemeLogic) -> dict:
    return upsert_theme_logic_mcp(theme.model_dump())


@app.post("/api/v2/proposals")
def create_tool_proposal(request: ToolProposalRequest) -> dict:
    return orchestrator.claude_agent.tool_registry.create_proposal(request.tool_name, request.payload)


@app.get("/api/v2/proposals/{proposal_id}")
def get_tool_proposal(proposal_id: str) -> dict:
    proposal = orchestrator.claude_agent.tool_registry.proposals.get(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return proposal


@app.post("/api/v2/proposals/{proposal_id}/approve")
def approve_tool_proposal(proposal_id: str) -> dict:
    return orchestrator.claude_agent.tool_registry.approve_proposal(proposal_id)


@app.get("/api/v2/audit/tools")
def list_tool_audit(limit: int = 100) -> dict:
    path = orchestrator.claude_agent.tool_registry.auditor.path
    if not path.exists():
        return {"items": []}
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        if line.strip():
            rows.append(json.loads(line))
    return {"items": rows}


@app.get("/api/v1/admin/themes")
def admin_list_themes() -> dict:
    return {"items": admin_service.list_themes()}


@app.get("/api/v1/admin/themes/{theme_name}")
def admin_get_theme(theme_name: str) -> dict:
    try:
        return admin_service.get_theme(theme_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"theme not found: {exc}") from exc


@app.put("/api/v1/admin/themes/{theme_name}")
def admin_save_theme(theme_name: str, theme: ThemeLogic) -> dict:
    if theme.theme_name != theme_name:
        raise HTTPException(status_code=400, detail="theme_name in path and body must match")
    return admin_service.save_theme(theme.model_dump())


@app.get("/api/v1/admin/docs")
def admin_list_docs() -> dict:
    return {"items": admin_service.list_knowledge_docs()}


@app.get("/api/v1/admin/docs/content")
def admin_get_doc(path: str) -> dict:
    try:
        return admin_service.get_knowledge_doc(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"doc not found: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/v1/admin/docs/content")
def admin_save_doc(request: KnowledgeDocUpdateRequest) -> dict:
    try:
        return admin_service.save_knowledge_doc(request.path, request.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/v1/admin/docs/content")
def admin_delete_doc(path: str, summary_mode: str = "investment") -> dict:
    try:
        if path.startswith("video_summaries/"):
            payload = content_ingest_service.delete_video_summary_by_path(path, summary_mode=summary_mode)
            if payload is not None:
                return payload | {"path": path, "delete_mode": "video_summary"}
            return admin_service.delete_knowledge_doc(path) | {"delete_mode": "video_summary_file_only"}
        return admin_service.delete_knowledge_doc(path) | {"delete_mode": "knowledge_doc"}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"doc not found: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/admin/factors")
def admin_list_factors() -> dict:
    from mcp_servers.factor_mining_server import list_factor_library

    return list_factor_library(limit=100)


@app.post("/api/v1/admin/factors/mine")
def admin_mine_factors(rounds: int | None = None, candidates_per_round: int | None = None) -> dict:
    """提交持久化因子挖掘任务，实际执行由 workers/job_worker.py 领取。"""
    payload = {key: value for key, value in {"rounds": rounds, "candidates_per_round": candidates_per_round}.items() if value is not None}
    task = JobTaskRepository().create("factor_mine", payload)
    return {"task_id": task["id"], "job_id": task["id"], "status": task["status"]}


@app.get("/api/v1/admin/factors/mine/{task_id}")
def admin_mine_factors_status(task_id: str) -> dict:
    """查询挖掘任务状态。"""
    task = JobTaskRepository().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    status_map = {"PENDING": "pending", "RUNNING": "running", "SUCCEEDED": "done", "FAILED_RETRYABLE": "failed", "FAILED_FINAL": "failed", "CANCELLED": "cancelled"}
    return {"status": status_map.get(task["status"], task["status"]), "result": _parse_job_result(task.get("result_ref")), "error": task.get("error")}


@app.post("/api/v2/factors/mine")
def submit_factor_mine_job(request: FactorMineRequest) -> dict:
    task = JobTaskRepository().create("factor_mine", request.model_dump(exclude_none=True))
    return {"job_id": task["id"], "status": task["status"]}


@app.get("/api/v2/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    task = JobTaskRepository().get(job_id)
    if task is None:
        raise HTTPException(status_code=404, detail="job not found")
    return task | {"result": _parse_job_result(task.get("result_ref"))}


@app.post("/api/v2/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    return {"job_id": job_id, "cancelled": JobTaskRepository().cancel(job_id)}


def _parse_job_result(value) -> dict | None:
    if not value:
        return None
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {"result_ref": value}


@app.get("/api/v1/admin/skills")
def admin_list_skills() -> dict:
    return {"items": admin_service.list_skills()}


@app.get("/api/v1/admin/skills/{slug}")
def admin_get_skill(slug: str) -> dict:
    try:
        return admin_service.get_skill(slug)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"skill not found: {exc}") from exc


@app.put("/api/v1/admin/skills/{slug}")
def admin_save_skill(slug: str, request: SkillUpdateRequest) -> dict:
    if request.slug != slug:
        raise HTTPException(status_code=400, detail="slug in path and body must match")
    try:
        return admin_service.save_skill(slug=request.slug, name=request.name, description=request.description, content=request.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/risk/portfolio")
def portfolio_risk(positions: list[Position]) -> dict:
    return evaluate_portfolio_risk(positions).model_dump()


@app.post("/api/v1/review/trade")
def review_trade(request: TradeReviewInput) -> dict:
    return {"status": "accepted", "review": request.model_dump(), "note": "MVP 版本返回结构化复盘输入，数据库写入由后续迁移接入。"}


def _format_sse(event: str, payload: dict) -> bytes:
    message = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    return message.encode("utf-8")
