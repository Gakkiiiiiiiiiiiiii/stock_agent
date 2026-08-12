from __future__ import annotations

from app.routers import factor


def test_factor_router_always_uses_remote_contract(monkeypatch):
    monkeypatch.setenv("FACTOR_SERVICE_URL", "http://factor.example")
    captured = {}

    def fake_create(self, request):
        captured["url"] = self.base_url
        captured["request"] = request.model_dump()
        return {"job_id": "remote-job", "status": "PENDING"}

    monkeypatch.setattr("clients.factor_client.RemoteFactorClient.create_mining_job", fake_create)
    payload = factor.submit_factor_mine_job(factor.FactorMineRequest(universe=["600000"], days=120))
    assert payload == {"job_id": "remote-job", "status": "PENDING"}
    assert captured["url"] == "http://factor.example"
    assert captured["request"]["symbols"] == ["600000"]
    assert captured["request"]["days"] == 120
