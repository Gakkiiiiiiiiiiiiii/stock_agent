"""API route contract regression for the read-only Decision Authority surface."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import app

# (method, path); external fact production and execution routes are excluded.
EXPECTED_ROUTES = {
    ("GET", "/health"),
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/health/version"),
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
    ("GET", "/api/v1/content/videos"),
    ("GET", "/api/v1/content/videos/{video_id}"),
    ("GET", "/api/v1/content/videos/{video_id}/summary-document"),
    ("GET", "/api/v1/content/videos/{video_id}/segments"),
    ("GET", "/api/v1/content/videos/{video_id}/chapters"),
    ("GET", "/api/v1/content/videos/{video_id}/knowledge"),
    ("GET", "/api/v1/content/videos/{video_id}/knowledge-units"),
    ("POST", "/api/v1/content/knowledge/search"),
    ("GET", "/api/v1/content/knowledge/{unit_id}"),
    ("GET", "/api/v1/content/knowledge-units/{unit_id}"),
    ("POST", "/api/v1/market/regime"),
    ("GET", "/api/v2/audit/tools"),
    ("GET", "/api/v1/admin/themes"),
    ("GET", "/api/v1/admin/themes/{theme_name}"),
    ("GET", "/api/v1/admin/docs"),
    ("GET", "/api/v1/admin/docs/content"),
    ("GET", "/api/v1/admin/factors"),
    ("GET", "/api/v1/admin/skills"),
    ("GET", "/api/v1/admin/skills/{slug}"),
    ("POST", "/api/v1/risk/portfolio"),
    ("POST", "/api/v1/review/trade"),
    ("POST", "/api/v1/decision/{decision_id}/replay"),
    ("POST", "/api/v1/decisions/{decision_id}/replay"),
    ("GET", "/api/v1/decisions/{decision_id}/snapshot"),
    ("POST", "/api/v1/decisions"),
    ("POST", "/api/v2/decisions"),
    ("GET", "/api/v2/decisions/{decision_id}/snapshot"),
    ("POST", "/api/v2/decisions/{decision_id}/outcomes/refresh"),
    ("GET", "/api/v2/decisions/{decision_id}/outcomes"),
    ("POST", "/api/v2/decisions/{decision_id}/review"),
    ("GET", "/api/v2/decisions/{decision_id}/review"),
    ("POST", "/api/v2/decisions/{decision_id}/replay"),
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


def test_key_endpoints_still_respond(monkeypatch):
    """代表性端点冒烟：健康检查 + 各域入口的方法/路径未漂移。"""
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    class Quant:
        def get_market_regime(self, *, as_of=None):
            return {"regime": {"primary_regime": "neutral"}, "source_system": "quant"}

    monkeypatch.setattr("app.dependencies.quant_client", Quant())
    response = client.post("/api/v1/market/regime", json={})
    assert response.status_code == 200
    assert "regime" in response.json()


def test_regime_route_rejects_naive_as_of_and_raw_fact_payload():
    client = TestClient(app)
    naive = client.post("/api/v1/market/regime", json={"as_of": "2026-08-24T09:30:00"})
    assert naive.status_code == 422
    injected = client.post(
        "/api/v1/market/regime",
        json={"snapshot": {"regime": "bull"}, "metrics": {"breadth": 1}},
    )
    assert injected.status_code == 422
