from clients.content_client import RemoteContentClient
from clients.factor_client import RemoteFactorClient
from contracts.factor import MiningJobRequest


def test_content_client_maps_legacy_facade_to_content_v1(monkeypatch):
    calls = []

    def fake_request(self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/api/v1/videos":
            return {"items": [{"video_id": "v1"}]}
        if path.endswith("/summary"):
            return {"data": {"video_id": "v1", "core_summary": "summary"}}
        return {"data": {"task_id": "t1", "status": "PENDING"}}

    monkeypatch.setattr(RemoteContentClient, "request", fake_request)
    client = RemoteContentClient("http://content.example")
    assert client.enqueue_bilibili(bv_id="BV1", summary_mode="investment")["task_id"] == "t1"
    assert client.list_videos()[0]["video_id"] == "v1"
    assert client.get_video_summary("v1")["core_summary"] == "summary"
    assert calls[0][1] == "/api/v1/videos/bilibili/ingest"
    assert calls[0][2]["payload"]["options"]["summary_mode"] == "investment"


def test_factor_client_maps_factor_v1_envelopes(monkeypatch):
    calls = []

    def fake_request(self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"contract_version": "factor.v1", "data": {"job_id": "j1", "status": "PENDING"}}

    monkeypatch.setattr(RemoteFactorClient, "request", fake_request)
    client = RemoteFactorClient("http://factor.example")
    result = client.create_mining_job(MiningJobRequest(symbols=["600000.SH"]))
    assert result == {"job_id": "j1", "status": "PENDING"}
    assert calls[0][1] == "/api/v1/mining/jobs"
    assert calls[0][2]["payload"]["symbols"] == ["600000.SH"]
