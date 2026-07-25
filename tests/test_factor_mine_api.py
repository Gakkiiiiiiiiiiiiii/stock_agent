from fastapi.testclient import TestClient
import time

from app.api import app
from workers.job_worker import heartbeat_loop, process_one_job

client = TestClient(app)


def test_factor_mine_task_flow(monkeypatch):
    import mcp_servers.factor_mining_server as factor_mining_server

    monkeypatch.setattr(
        factor_mining_server,
        "mine_factors",
        lambda **kwargs: {"accepted": [{"id": "F001"}], "rejected": [], "warning": None},
    )
    response = client.post("/api/v1/admin/factors/mine")
    assert response.status_code == 200
    task_id = response.json()["task_id"]
    assert len(task_id) == 36

    assert process_one_job("test-worker", job_id=task_id) is True
    data = client.get(f"/api/v1/admin/factors/mine/{task_id}").json()
    assert data["status"] == "done"
    assert data["result"]["accepted"] == [{"id": "F001"}]
    assert data["error"] is None


def test_factor_mine_task_failure(monkeypatch):
    """后台任务抛异常（如容器内无法访问 QMT）时应优雅落 failed + error。"""
    import mcp_servers.factor_mining_server as factor_mining_server

    def boom(**kwargs):
        raise RuntimeError("QMT 数据源不可用")

    monkeypatch.setattr(factor_mining_server, "mine_factors", boom)
    task_id = client.post("/api/v1/admin/factors/mine").json()["task_id"]
    assert process_one_job("test-worker", job_id=task_id) is True
    data = client.get(f"/api/v1/admin/factors/mine/{task_id}").json()
    assert data["status"] == "failed"
    assert "QMT" in data["error"]["message"]
    assert data["result"] is None


def test_factor_mine_unknown_task():
    response = client.get("/api/v1/admin/factors/mine/deadbeef")
    assert response.status_code == 404


def test_heartbeat_loop_updates_while_job_runs():
    calls = []

    class Repo:
        def heartbeat(self, task_id, worker_id, progress=None):
            calls.append((task_id, worker_id, progress))

    with heartbeat_loop(Repo(), "task-1", "worker-1", interval=0.01):
        time.sleep(0.035)
    assert len(calls) >= 2
