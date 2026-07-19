from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import app


class FakeAdminService:
    def delete_knowledge_doc(self, path):
        assert path == "video_summaries/orphan.md"
        return {"deleted": True, "path": path, "title": "orphan"}


class FakeContentService:
    def __init__(self, task_status="processing"):
        self.task_status = task_status
        self.started_task_ids = []

    def enqueue_bilibili(self, **kwargs):
        assert kwargs["url"] == "https://www.bilibili.com/video/BVTEST123"
        return {"task_id": 1, "video_id": 2, "status": "pending", "stage": "queued", "deduplicated": False}

    def process_task(self, task_id):
        assert task_id == 1
        self.started_task_ids.append(task_id)
        return {
            "video": {"id": 2, "title": "测试视频", "transcript_status": "success"},
            "summary": {"core_summary": "摘要"},
            "segments": [],
            "chunks": [],
            "events": [],
        }

    def get_task(self, task_id):
        assert task_id == 1
        stage = "queued" if self.task_status == "pending" else "asr"
        progress = 0 if self.task_status == "pending" else 50
        return {"task_id": 1, "video_id": 2, "status": self.task_status, "stage": stage, "progress": progress, "error_message": None}

    def get_video_detail(self, video_id, summary_mode="investment"):
        assert video_id == 2
        assert summary_mode == "investment"
        return {"video": {"id": 2, "title": "测试视频"}, "summary": {"core_summary": "摘要"}, "segments": [], "chunks": [], "events": [], "event_timeline": []}

    def list_videos(self, summary_mode="investment", limit=50):
        assert summary_mode == "investment"
        assert limit == 50
        return [{"video_id": 2, "title": "测试视频", "bvid": "BVTEST123", "summary_ready": True}]

    def get_video_summary_document(self, video_id, summary_mode="investment"):
        assert video_id == 2
        assert summary_mode == "investment"
        return {"video_id": 2, "title": "测试视频", "path": "knowledge_base/video_summaries/test.md", "content": "# 测试视频\n\n摘要"}

    def delete_video_summary(self, video_id, summary_mode="investment"):
        assert video_id == 2
        assert summary_mode == "investment"
        return {"deleted": True, "video_id": 2, "removed_markdown": True, "deleted_memory_ids": [77, 88]}

    def delete_video_summary_by_path(self, summary_path, summary_mode="investment", target_collection="financial_knowledge"):
        assert summary_path in {"video_summaries/test.md", "video_summaries/orphan.md"}
        assert summary_mode == "investment"
        assert target_collection == "financial_knowledge"
        if summary_path == "video_summaries/orphan.md":
            return None
        return {"deleted": True, "video_id": 2, "removed_markdown": True, "deleted_memory_ids": [77, 88]}

    def get_video_segments(self, video_id):
        assert video_id == 2
        return {"video_id": 2, "segments": [{"segment_index": 0, "text": "测试"}]}

    def get_video_events(self, video_id, summary_mode="investment"):
        assert video_id == 2
        assert summary_mode == "investment"
        return {"video_id": 2, "chunks": [{"chunk_index": 0}], "events": [{"event_type": "OPINION"}], "timeline": [{"statement": "测试"}]}

    def get_video_frame_image_path(self, video_id, frame_index):
        assert video_id == 2
        assert frame_index == 1
        return __file__

    def get_video_frame_image_path_by_filename(self, bvid, filename):
        assert bvid == "BVTEST123"
        assert filename == "BVTEST123_000001.jpg"
        return __file__


client = TestClient(app)


def test_content_ingest_api(monkeypatch):
    monkeypatch.setattr("app.api.content_ingest_service", FakeContentService())
    response = client.post("/api/v1/content/bilibili/ingest", json={"url": "https://www.bilibili.com/video/BVTEST123"})
    assert response.status_code == 200
    assert response.json()["task_id"] == 1


def test_content_task_process_api_starts_background_task(monkeypatch):
    fake_service = FakeContentService(task_status="pending")
    monkeypatch.setattr("app.api.content_ingest_service", fake_service)
    response = client.post("/api/v1/content/tasks/1/process")
    assert response.status_code == 200
    assert response.json()["started"] is True
    assert fake_service.started_task_ids == [1]


def test_content_task_and_video_api(monkeypatch):
    monkeypatch.setattr("app.api.content_ingest_service", FakeContentService())
    task = client.get("/api/v1/content/tasks/1")
    videos = client.get("/api/v1/content/videos")
    video = client.get("/api/v1/content/videos/2")
    document = client.get("/api/v1/content/videos/2/summary-document")
    deleted = client.delete("/api/v1/content/videos/2/summary")
    segments = client.get("/api/v1/content/videos/2/segments")
    events = client.get("/api/v1/content/videos/2/events")
    frame = client.get("/api/v1/content/videos/2/frames/1/image")
    frame_by_filename = client.get("/api/v1/content/video-frames/BVTEST123/BVTEST123_000001.jpg")
    assert task.status_code == 200
    assert videos.status_code == 200
    assert video.status_code == 200
    assert document.status_code == 200
    assert deleted.status_code == 200
    assert segments.status_code == 200
    assert events.status_code == 200
    assert frame.status_code == 200
    assert frame_by_filename.status_code == 200
    assert task.json()["stage"] == "asr"
    assert videos.json()["items"][0]["bvid"] == "BVTEST123"
    assert video.json()["video"]["title"] == "测试视频"
    assert document.json()["content"].startswith("# 测试视频")
    assert deleted.json()["deleted"] is True
    assert segments.json()["segments"][0]["text"] == "测试"
    assert events.json()["events"][0]["event_type"] == "OPINION"


def test_content_summarize_api(monkeypatch):
    monkeypatch.setattr("app.api.content_ingest_service", FakeContentService())
    response = client.post("/api/v1/content/bilibili/summarize", json={"url": "https://www.bilibili.com/video/BVTEST123"})
    assert response.status_code == 200
    assert response.json()["summary"]["core_summary"] == "摘要"


def test_admin_delete_video_summary_doc_routes_to_content_service(monkeypatch):
    monkeypatch.setattr("app.api.content_ingest_service", FakeContentService())
    response = client.delete("/api/v1/admin/docs/content", params={"path": "video_summaries/test.md"})
    assert response.status_code == 200
    assert response.json()["delete_mode"] == "video_summary"
    assert response.json()["deleted"] is True


def test_admin_delete_video_summary_doc_falls_back_to_file_delete(monkeypatch):
    monkeypatch.setattr("app.api.content_ingest_service", FakeContentService())
    monkeypatch.setattr("app.api.admin_service", FakeAdminService())
    response = client.delete("/api/v1/admin/docs/content", params={"path": "video_summaries/orphan.md"})
    assert response.status_code == 200
    assert response.json()["delete_mode"] == "video_summary_file_only"
    assert response.json()["deleted"] is True
