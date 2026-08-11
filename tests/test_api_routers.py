"""§28 API 拆分回归：路由拆分后对外路由表与原 app/api.py 单文件完全一致。

期望清单来自重构前 app/api.py（git HEAD）并包含本轮 P2 新增的执行和实时
接口；任何路由的新增/删除/方法变更都会使本测试失败，防止拆分时意外改变
外部契约。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import app

# (method, path)，与重构前 app/api.py 的路由一一对应。
EXPECTED_ROUTES = {
    ("GET", "/health"),
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/metrics"),
    ("GET", "/admin"),
    ("POST", "/api/v1/analyze/stock"),
    ("POST", "/api/v1/analyze/theme"),
    ("POST", "/api/v1/market/daily-scan"),
    ("POST", "/api/v1/agent/run"),
    ("POST", "/api/v1/agent/run/stream"),
    ("GET", "/api/v1/agent/sessions"),
    ("POST", "/api/v1/agent/sessions"),
    ("GET", "/api/v1/agent/sessions/{session_id}"),
    ("DELETE", "/api/v1/agent/sessions/{session_id}"),
    ("POST", "/api/v1/retrieval/context"),
    ("POST", "/api/v1/content/bilibili/ingest"),
    ("POST", "/api/v1/content/bilibili/summarize"),
    ("POST", "/api/v1/content/xiaoe/hls/ingest"),
    ("POST", "/api/v1/content/xiaoe/hls/summarize"),
    ("GET", "/api/v1/content/tasks/{task_id}"),
    ("POST", "/api/v1/content/tasks/{task_id}/process"),
    ("GET", "/api/v1/content/videos"),
    ("GET", "/api/v1/content/videos/{video_id}"),
    ("GET", "/api/v1/content/videos/{video_id}/summary-document"),
    ("DELETE", "/api/v1/content/videos/{video_id}/summary"),
    ("GET", "/api/v1/content/videos/{video_id}/segments"),
    ("GET", "/api/v1/content/videos/{video_id}/events"),
    ("GET", "/api/v1/content/videos/{video_id}/chapters"),
    ("GET", "/api/v1/content/videos/{video_id}/chapters/{chapter_id}"),
    ("GET", "/api/v1/content/videos/{video_id}/knowledge"),
    ("GET", "/api/v1/content/videos/{video_id}/knowledge-units"),
    ("POST", "/api/v1/content/knowledge/search"),
    ("GET", "/api/v1/content/knowledge/conflicts"),
    ("GET", "/api/v1/content/knowledge/subjects/{subject_key}/current"),
    ("GET", "/api/v1/content/knowledge/subjects/{subject_key}/history"),
    ("POST", "/api/v1/content/knowledge/lifecycle/sweep"),
    ("POST", "/api/v1/content/knowledge/lifecycle/sweep-task"),
    ("GET", "/api/v1/content/knowledge/{unit_id}"),
    ("GET", "/api/v1/content/knowledge-units/{unit_id}"),
    ("POST", "/api/v1/content/videos/{video_id}/reparse"),
    ("GET", "/api/v1/content/knowledge/{unit_id}/verifications"),
    ("GET", "/api/v1/content/knowledge/{unit_id}/evidence"),
    ("PATCH", "/api/v1/content/knowledge/{unit_id}/lifecycle"),
    ("PATCH", "/api/v1/content/knowledge-units/{unit_id}/lifecycle"),
    ("GET", "/api/v1/content/knowledge-units/{unit_id}/lifecycle-audits"),
    ("GET", "/api/v1/content/videos/{video_id}/frames/{frame_index}/image"),
    ("GET", "/api/v1/content/video-frames/{bvid}/{filename}"),
    ("POST", "/api/v1/market/regime"),
    ("POST", "/api/v1/knowledge/theme"),
    ("POST", "/api/v2/proposals"),
    ("GET", "/api/v2/proposals/{proposal_id}"),
    ("POST", "/api/v2/proposals/{proposal_id}/approve"),
    ("GET", "/api/v2/audit/tools"),
    ("GET", "/api/v1/admin/themes"),
    ("GET", "/api/v1/admin/themes/{theme_name}"),
    ("PUT", "/api/v1/admin/themes/{theme_name}"),
    ("GET", "/api/v1/admin/docs"),
    ("GET", "/api/v1/admin/docs/content"),
    ("PUT", "/api/v1/admin/docs/content"),
    ("DELETE", "/api/v1/admin/docs/content"),
    ("GET", "/api/v1/admin/factors"),
    ("POST", "/api/v1/admin/factors/mine"),
    ("GET", "/api/v1/admin/factors/mine/{task_id}"),
    ("POST", "/api/v2/factors/mine"),
    ("GET", "/api/v2/jobs/{job_id}"),
    ("POST", "/api/v2/jobs/{job_id}/cancel"),
    ("GET", "/api/v1/admin/skills"),
    ("GET", "/api/v1/admin/skills/{slug}"),
    ("PUT", "/api/v1/admin/skills/{slug}"),
    ("POST", "/api/v1/risk/portfolio"),
    ("POST", "/api/v1/review/trade"),
    ("POST", "/api/v1/decision/{decision_id}/replay"),
    ("POST", "/api/v1/execution/orders"),
    ("POST", "/api/v1/execution/orders/{client_order_id}/submit"),
    ("POST", "/api/v1/execution/orders/{client_order_id}/fills"),
    ("POST", "/api/v1/execution/reconcile"),
    ("POST", "/api/v1/stream/market-events"),
    ("GET", "/api/v1/stream/market-features/{symbol}"),
}


def _walk(routes):
    for route in routes:
        if type(route).__name__ == "_IncludedRouter":
            yield from _walk(route.original_router.routes)
        else:
            yield route


def _current_routes() -> set[tuple[str, str]]:
    builtin = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    return {
        (method, route.path)
        for route in _walk(app.routes)
        for method in (getattr(route, "methods", None) or set())
        if method in {"GET", "POST", "PUT", "PATCH", "DELETE"} and route.path not in builtin
    }


def test_route_table_matches_pre_split_contract():
    assert _current_routes() == EXPECTED_ROUTES


def test_key_endpoints_still_respond():
    """代表性端点冒烟：健康检查 + 各域入口的方法/路径未漂移。"""
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    response = client.post("/api/v1/market/regime", json={})
    assert response.status_code == 200
    assert "regime" in response.json()
