from __future__ import annotations

from app.routers import factor


def test_factor_router_keeps_legacy_job_path_by_default(monkeypatch):
    monkeypatch.delenv("FACTOR_BACKEND", raising=False)
    class FakeRepository:
        def create(self, *_args, **_kwargs):
            return {"id": "legacy-job", "status": "pending"}
    monkeypatch.setattr(factor, "JobTaskRepository", FakeRepository)
    assert factor.submit_factor_mine_job(factor.FactorMineRequest()) == {"job_id": "legacy-job", "status": "pending"}


def test_factor_router_uses_remote_contract_when_selected(monkeypatch):
    monkeypatch.setenv("FACTOR_BACKEND", "remote")
    monkeypatch.setenv("FACTOR_SERVICE_URL", "http://factor.example")
    captured = {}

    def fake_create(self, request):
        captured["url"] = self.base_url
        captured["request"] = request.model_dump()
        return {"job_id": "remote-job", "status": "PENDING"}

    monkeypatch.setattr(factor.RemoteFactorClient, "create_mining_job", fake_create)
    payload = factor.submit_factor_mine_job(factor.FactorMineRequest(universe=["600000"], days=120))
    assert payload == {"job_id": "remote-job", "status": "PENDING"}
    assert captured == {"url": "http://factor.example", "request": {"rounds": None, "candidates_per_round": None, "symbols": ["600000"], "days": 120, "eval_window": None}}
