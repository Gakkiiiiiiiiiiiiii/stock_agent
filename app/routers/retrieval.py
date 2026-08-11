"""检索路由（从 app/api.py 平移，路由契约不变）。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class RetrievalRequest(BaseModel):
    query: str
    task_type: str | None = None
    filters: dict | None = None
    top_k: int = 5


@router.post("/api/v1/retrieval/context")
def retrieve_context(request: RetrievalRequest) -> dict:
    from mcp_servers.retrieval_server import retrieve_relevant_context

    return retrieve_relevant_context(query=request.query, task_type=request.task_type, filters=request.filters, top_k=request.top_k)
