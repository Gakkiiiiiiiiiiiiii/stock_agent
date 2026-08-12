from fastapi.testclient import TestClient

from app import dependencies
from app.api import app

client = TestClient(app)


class FakeFactorClient:
    def create_mining_job(self, request):
        return {"job_id": "remote-job", "status": "PENDING", "rounds": request.rounds}

    def get_mining_job(self, job_id):
        return {"job_id": job_id, "status": "SUCCEEDED"}


def test_admin_factor_mine_proxies_remote_service(monkeypatch):
    monkeypatch.setattr(dependencies, "factor_client", FakeFactorClient())
    response = client.post("/api/v1/admin/factors/mine?rounds=12")
    assert response.status_code == 200
    assert response.json() == {"job_id": "remote-job", "status": "PENDING", "rounds": 12}


def test_admin_factor_mine_status_proxies_remote_service(monkeypatch):
    monkeypatch.setattr(dependencies, "factor_client", FakeFactorClient())
    response = client.get("/api/v1/admin/factors/mine/remote-job")
    assert response.status_code == 200
    assert response.json()["status"] == "SUCCEEDED"
