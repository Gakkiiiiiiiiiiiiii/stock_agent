"""Admin 控制台 / 主题 / 文档 / 因子 / 技能管理与工具提案审计路由（从 app/api.py 平移，路由契约不变）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import dependencies
from app.routers._shared import _parse_job_result
from financial_agent.models import ThemeLogic
from storage.repositories.job_repository import JobTaskRepository
from workers.job_types import JobType

router = APIRouter()


class KnowledgeDocUpdateRequest(BaseModel):
    path: str
    content: str


class ToolProposalRequest(BaseModel):
    tool_name: str
    payload: dict


class SkillUpdateRequest(BaseModel):
    slug: str
    name: str
    description: str = ""
    content: str


@router.get("/admin")
def admin_console() -> FileResponse:
    return FileResponse(
        dependencies.admin_service.root / "app" / "static" / "admin.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/api/v1/admin/themes")
def admin_list_themes() -> dict:
    return {"items": dependencies.admin_service.list_themes()}


@router.get("/api/v1/admin/themes/{theme_name}")
def admin_get_theme(theme_name: str) -> dict:
    try:
        return dependencies.admin_service.get_theme(theme_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"theme not found: {exc}") from exc


@router.put("/api/v1/admin/themes/{theme_name}")
def admin_save_theme(theme_name: str, theme: ThemeLogic) -> dict:
    if theme.theme_name != theme_name:
        raise HTTPException(status_code=400, detail="theme_name in path and body must match")
    return dependencies.admin_service.save_theme(theme.model_dump())


@router.get("/api/v1/admin/docs")
def admin_list_docs() -> dict:
    return {"items": dependencies.admin_service.list_knowledge_docs()}


@router.get("/api/v1/admin/docs/content")
def admin_get_doc(path: str) -> dict:
    try:
        return dependencies.admin_service.get_knowledge_doc(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"doc not found: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/api/v1/admin/docs/content")
def admin_save_doc(request: KnowledgeDocUpdateRequest) -> dict:
    try:
        return dependencies.admin_service.save_knowledge_doc(request.path, request.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/v1/admin/docs/content")
def admin_delete_doc(path: str, summary_mode: str = "investment") -> dict:
    try:
        if path.startswith("video_summaries/"):
            payload = dependencies.content_ingest_service.delete_video_summary_by_path(path, summary_mode=summary_mode)
            if payload is not None:
                return payload | {"path": path, "delete_mode": "video_summary"}
            return dependencies.admin_service.delete_knowledge_doc(path) | {"delete_mode": "video_summary_file_only"}
        return dependencies.admin_service.delete_knowledge_doc(path) | {"delete_mode": "knowledge_doc"}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"doc not found: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/v1/admin/factors")
def admin_list_factors() -> dict:
    from mcp_servers.factor_mining_server import list_factor_library

    return list_factor_library(limit=100)


@router.post("/api/v1/admin/factors/mine")
def admin_mine_factors(rounds: int | None = None, candidates_per_round: int | None = None) -> dict:
    """提交持久化因子挖掘任务，实际执行由 workers/job_worker.py 领取。"""
    payload = {key: value for key, value in {"rounds": rounds, "candidates_per_round": candidates_per_round}.items() if value is not None}
    task = JobTaskRepository().create(JobType.FACTOR_MINE, payload)
    return {"task_id": task["id"], "job_id": task["id"], "status": task["status"]}


@router.get("/api/v1/admin/factors/mine/{task_id}")
def admin_mine_factors_status(task_id: str) -> dict:
    """查询挖掘任务状态。"""
    task = JobTaskRepository().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    status_map = {"PENDING": "pending", "RUNNING": "running", "SUCCEEDED": "done", "FAILED_RETRYABLE": "failed", "FAILED_FINAL": "failed", "CANCELLED": "cancelled"}
    return {"status": status_map.get(task["status"], task["status"]), "result": _parse_job_result(task.get("result_ref")), "error": task.get("error")}


@router.get("/api/v1/admin/skills")
def admin_list_skills() -> dict:
    return {"items": dependencies.admin_service.list_skills()}


@router.get("/api/v1/admin/skills/{slug}")
def admin_get_skill(slug: str) -> dict:
    try:
        return dependencies.admin_service.get_skill(slug)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"skill not found: {exc}") from exc


@router.put("/api/v1/admin/skills/{slug}")
def admin_save_skill(slug: str, request: SkillUpdateRequest) -> dict:
    if request.slug != slug:
        raise HTTPException(status_code=400, detail="slug in path and body must match")
    try:
        return dependencies.admin_service.save_skill(slug=request.slug, name=request.name, description=request.description, content=request.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/v2/proposals")
def create_tool_proposal(request: ToolProposalRequest) -> dict:
    return dependencies.orchestrator.claude_agent.tool_registry.create_proposal(request.tool_name, request.payload)


@router.get("/api/v2/proposals/{proposal_id}")
def get_tool_proposal(proposal_id: str) -> dict:
    proposal = dependencies.orchestrator.claude_agent.tool_registry.proposals.get(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return proposal


@router.post("/api/v2/proposals/{proposal_id}/approve")
def approve_tool_proposal(proposal_id: str) -> dict:
    return dependencies.orchestrator.claude_agent.tool_registry.approve_proposal(proposal_id)


@router.get("/api/v2/audit/tools")
def list_tool_audit(limit: int = 100) -> dict:
    path = dependencies.orchestrator.claude_agent.tool_registry.auditor.path
    if not path.exists():
        return {"items": []}
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        if line.strip():
            rows.append(json.loads(line))
    return {"items": rows}
