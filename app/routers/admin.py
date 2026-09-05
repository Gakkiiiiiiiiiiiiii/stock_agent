"""Read-only admin metadata and tool-audit routes for the Decision Authority."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app import dependencies

router = APIRouter()


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


@router.get("/api/v1/admin/factors")
def admin_list_factors() -> dict:
    return dependencies.factor_client.list_factors(limit=100)


@router.get("/api/v1/admin/skills")
def admin_list_skills() -> dict:
    return {"items": dependencies.admin_service.list_skills()}


@router.get("/api/v1/admin/skills/{slug}")
def admin_get_skill(slug: str) -> dict:
    try:
        return dependencies.admin_service.get_skill(slug)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"skill not found: {exc}") from exc


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
