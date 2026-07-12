from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date as Date
from queue import Queue
from threading import Thread

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.admin_service import AdminContentService
from app.agent_orchestrator import AgentOrchestrator
from app.chat_history_service import ChatHistoryService
from app.dependencies import init_application
from engines.content.video_ingest_service import VideoIngestService
from engines.risk.portfolio_risk import evaluate_portfolio_risk
from financial_agent.models import Position, ThemeLogic, TradeReviewInput
from mcp_servers.knowledge_server import upsert_theme_logic as upsert_theme_logic_mcp


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_application()
    yield


app = FastAPI(title="Financial Analysis Agent", version="0.1.0", lifespan=lifespan)
orchestrator = AgentOrchestrator()
admin_service = AdminContentService()
chat_history_service = ChatHistoryService()
content_ingest_service = VideoIngestService()


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
    up_count: int = 2400
    down_count: int = 1800
    index_return_5d: float = 0.01
    index_return_20d: float = 0.03
    top_theme_strength: float = 72
    limit_up_count: int = 28
    index_volatility: float = 0.02
    index_drawdown_20d: float = -0.04
    limit_down_count: int = 8
    previous_regime: str | None = None


class KnowledgeDocUpdateRequest(BaseModel):
    path: str
    content: str


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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/admin")
def admin_console() -> FileResponse:
    return FileResponse(admin_service.root / "app" / "static" / "admin.html")


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


@app.get("/api/v1/content/tasks/{task_id}")
def get_content_task(task_id: int) -> dict:
    task = content_ingest_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@app.get("/api/v1/content/videos")
def list_content_videos(summary_mode: str = "investment", limit: int = 50) -> dict:
    return {"items": content_ingest_service.list_videos(summary_mode=summary_mode, limit=limit)}


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


@app.get("/api/v1/content/videos/{video_id}/frames/{frame_index}/image")
def get_content_video_frame_image(video_id: int, frame_index: int) -> FileResponse:
    image_path = content_ingest_service.get_video_frame_image_path(video_id, frame_index)
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
