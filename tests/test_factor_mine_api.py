import time

from fastapi.testclient import TestClient

from app.api import app

client = TestClient(app)


def _wait_task(task_id: str, timeout: float = 10.0) -> dict:
    """轮询任务状态直到结束（done/failed）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/v1/admin/factors/mine/{task_id}")
        assert response.status_code == 200
        data = response.json()
        if data["status"] != "running":
            return data
        time.sleep(0.05)
    raise AssertionError(f"任务 {task_id} 未在 {timeout}s 内结束")


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
    assert len(task_id) == 8

    data = _wait_task(task_id)
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
    data = _wait_task(task_id)
    assert data["status"] == "failed"
    assert "QMT" in data["error"]
    assert data["result"] is None


def test_factor_mine_unknown_task():
    response = client.get("/api/v1/admin/factors/mine/deadbeef")
    assert response.status_code == 404
