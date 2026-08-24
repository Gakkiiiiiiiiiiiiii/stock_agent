from fastapi.testclient import TestClient

from app.api import app


def test_factor_mining_routes_are_not_exposed():
    client = TestClient(app)
    assert client.post("/api/v2/factors/mine").status_code == 404
    assert client.post("/api/v1/admin/factors/mine").status_code == 404
    assert client.get("/api/v1/admin/factors/mine/job").status_code == 404
    assert client.post("/api/v2/jobs/job/cancel").status_code == 404
