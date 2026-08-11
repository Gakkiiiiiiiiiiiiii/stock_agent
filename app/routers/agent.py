"""Agent 执行与会话路由（从 app/api.py 平移，路由契约不变）。"""
from __future__ import annotations

from datetime import date as Date
from queue import Queue
from threading import Thread

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import dependencies
from app.routers._shared import _format_sse

router = APIRouter()


class StockAnalyzeRequest(BaseModel):
    symbol: str
    date: Date | None = None
    analysis_type: str = "full"
    patterns: list[str] | None = None


class ThemeAnalyzeRequest(BaseModel):
    theme_name: str
    date: Date | None = None


class AgentRunRequest(BaseModel):
    query: str
    context: dict | None = None
    skill: str | None = None
    session_id: str | None = None


class AgentSessionCreateRequest(BaseModel):
    title: str | None = None


@router.post("/api/v1/analyze/stock")
def analyze_stock(request: StockAnalyzeRequest) -> dict:
    return dependencies.orchestrator.analyze_stock(request.symbol, as_of=request.date, patterns=request.patterns)


@router.post("/api/v1/analyze/theme")
def analyze_theme(request: ThemeAnalyzeRequest) -> dict:
    return dependencies.orchestrator.analyze_theme(request.theme_name)


@router.post("/api/v1/agent/run")
def run_agent(request: AgentRunRequest) -> dict:
    session = dependencies.chat_history_service.ensure_session(request.session_id, title_hint=request.query)
    payload = dependencies.orchestrator.run_agent(query=request.query, context=request.context, skill=request.skill)
    dependencies.chat_history_service.save_turn(
        session_id=session["session_id"],
        user_query=request.query,
        assistant_content=payload.get("report") or payload.get("warning") or "",
        response=payload,
    )
    payload["session_id"] = session["session_id"]
    return payload


@router.post("/api/v1/agent/run/stream")
def run_agent_stream(request: AgentRunRequest) -> StreamingResponse:
    event_queue: Queue[tuple[str, dict] | None] = Queue()
    session = dependencies.chat_history_service.ensure_session(request.session_id, title_hint=request.query)

    def emit(event: str, payload: dict) -> None:
        event_queue.put((event, payload))

    def worker() -> None:
        try:
            emit("session", {"session_id": session["session_id"], "title": session.get("title")})
            payload = dependencies.orchestrator.run_agent(query=request.query, context=request.context, skill=request.skill, emit=emit)
            dependencies.chat_history_service.save_turn(
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


@router.get("/api/v1/agent/sessions")
def list_agent_sessions() -> dict:
    return {"items": dependencies.chat_history_service.list_sessions()}


@router.post("/api/v1/agent/sessions")
def create_agent_session(request: AgentSessionCreateRequest) -> dict:
    return dependencies.chat_history_service.create_session(title=request.title)


@router.get("/api/v1/agent/sessions/{session_id}")
def get_agent_session(session_id: str) -> dict:
    try:
        return dependencies.chat_history_service.get_session(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"session not found: {exc}") from exc


@router.delete("/api/v1/agent/sessions/{session_id}")
def delete_agent_session(session_id: str) -> dict:
    try:
        dependencies.chat_history_service.delete_session(session_id)
        return {"deleted": True, "session_id": session_id}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"session not found: {exc}") from exc
