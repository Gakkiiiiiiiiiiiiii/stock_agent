"""检索路由（从 app/api.py 平移，路由契约不变）。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app import dependencies

router = APIRouter()


class RetrievalRequest(BaseModel):
    query: str
    task_type: str | None = None
    filters: dict | None = None
    top_k: int = 5


@router.post("/api/v1/retrieval/context")
def retrieve_context(request: RetrievalRequest) -> dict:
    return dependencies.content_client.search_video_knowledge(request.query, filters=request.filters, limit=request.top_k, intent=request.task_type)
