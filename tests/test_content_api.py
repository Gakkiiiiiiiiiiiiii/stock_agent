from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import app
from storage.repositories.content_repository import ContentQueryRepository, ContentTaskRepository


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

    def enqueue_xiaoe_hls(self, **kwargs):
        assert kwargs["m3u8_url"] == "https://media.example.com/v_abc/index.m3u8"
        assert kwargs["authorized_content"] is True
        return {"task_id": 3, "video_id": 4, "status": "pending", "stage": "queued", "deduplicated": False}

    def process_task(self, task_id):
        assert task_id in {1, 3}
        self.started_task_ids.append(task_id)
        return {
            "video": {"id": 2 if task_id == 1 else 4, "title": "测试视频", "transcript_status": "success"},
            "summary": {"core_summary": "摘要"},
            "segments": [],
            "chunks": [],
            "events": [],
        }

    def get_task(self, task_id):
        assert task_id in {1, 3}
        stage = "queued" if self.task_status == "pending" else "asr"
        progress = 0 if self.task_status == "pending" else 50
        return {"task_id": task_id, "video_id": 2 if task_id == 1 else 4, "status": self.task_status, "stage": stage, "progress": progress, "error_message": None}

    def get_video_detail(self, video_id, summary_mode="investment"):
        assert video_id == 2
        assert summary_mode == "investment"
        return {"video": {"id": 2, "title": "测试视频"}, "summary": {"core_summary": "摘要"}, "segments": [], "chunks": [], "events": [], "event_timeline": []}

    def list_videos(self, summary_mode="investment", limit=50):
        assert summary_mode == "investment"
        assert limit in {50, 200}
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

    def get_video_chapters(self, video_id):
        assert video_id == 2
        return {"video_id": 2, "chapters": [{"id": 10, "title": "章节"}]}

    def get_video_chapter(self, video_id, chapter_id):
        assert video_id == 2
        assert chapter_id == 10
        return {"id": 10, "title": "章节", "knowledge_units": []}

    def list_video_knowledge_units(self, video_id, filters=None, limit=None):
        assert video_id == 2
        return {"video_id": 2, "items": [{"id": 99, "subject_key": filters.get("subject_key") if filters else None}], "limit": limit, "next_cursor": None, "filters": filters or {}, "warnings": []}

    def get_knowledge_unit(self, unit_id):
        assert unit_id == 99
        return {"id": 99, "canonical_statement": "知识"}

    def search_video_knowledge(self, query, filters=None, limit=20):
        return {"query": query, "filters": filters or {}, "items": [{"id": 99}], "limit": limit, "next_cursor": None, "warnings": []}

    def reparse_video_knowledge(self, video_id, index_knowledge=True):
        assert video_id == 2
        return {"task": {"task_id": 8}, "knowledge_result": {"vector_tasks": [] if not index_knowledge else [1]}}

    def update_knowledge_unit_lifecycle(self, unit_id, lifecycle_status=None, verification_status=None, valid_to=None, note=None, operator=None):
        assert unit_id == 99
        return {
            "id": 99,
            "lifecycle_status": lifecycle_status,
            "verification_status": verification_status,
            "valid_to": valid_to.isoformat() if valid_to else None,
            "note": note,
            "operator": operator,
            "vector_tasks": [{"task_id": 11}],
        }

    def list_knowledge_conflicts(self, subject_key=None, limit=50):
        return {"items": [{"conflict_group_id": "cg1", "subject_key": subject_key}], "limit": limit, "next_cursor": None, "filters": {"subject_key": subject_key} if subject_key else {}, "warnings": []}

    def expire_due_knowledge_units(self, now=None, limit=500):
        return {"expired_count": 1, "limit": limit, "as_of": now.isoformat() if now else None, "items": [{"id": 99}], "vector_tasks": [{"task_id": 12}]}

    def list_knowledge_unit_lifecycle_audits(self, unit_id, limit=50):
        assert unit_id == 99
        return {"knowledge_unit_id": unit_id, "items": [{"id": 1, "to_lifecycle_status": "RETIRED"}], "limit": limit, "next_cursor": None, "warnings": []}

    def get_current_subject_state(self, subject_key, domain=None, limit=20):
        return {"subject_key": subject_key, "domain": domain, "items": [], "limit": limit, "next_cursor": None, "filters": {"subject_key": subject_key, "domain": domain}, "warnings": []}

    def get_subject_history(self, subject_key, domain=None, limit=50):
        return {"subject_key": subject_key, "domain": domain, "items": [], "limit": limit, "next_cursor": None, "filters": {"subject_key": subject_key, "domain": domain}, "warnings": []}

    def get_video_frame_image_path(self, video_id, frame_index):
        assert video_id == 2
        assert frame_index == 1
        return __file__

    def get_video_frame_image_path_by_filename(self, bvid, filename):
        assert bvid == "BVTEST123"
        assert filename == "BVTEST123_000001.jpg"
        return __file__


client = TestClient(app)


def test_summary_document_prefers_analysis_document_over_legacy_summary(monkeypatch):
    repo = ContentQueryRepository()
    monkeypatch.setattr(
        repo,
        "get_video_detail",
        lambda video_id, summary_mode="investment": {
            "video": {"title": "测试视频"},
            "summary": {"summary_markdown": "# 旧总结"},
            "summary_export_path": None,
            "analysis_document": {"document_markdown": "# 新 K3 总结"},
        },
    )

    document = repo.get_video_summary_document(2)

    assert document["source"] == "analysis_document"
    assert document["content"] == "# 新 K3 总结"


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
    chapters = client.get("/api/v1/content/videos/2/chapters")
    chapter = client.get("/api/v1/content/videos/2/chapters/10")
    knowledge = client.get("/api/v1/content/videos/2/knowledge", params={"subject_key": "券商"})
    knowledge_alias = client.get("/api/v1/content/videos/2/knowledge-units", params={"subject_key": "券商"})
    unit = client.get("/api/v1/content/knowledge/99")
    unit_alias = client.get("/api/v1/content/knowledge-units/99")
    search = client.post("/api/v1/content/knowledge/search", json={"query": "券商", "limit": 3})
    reparse = client.post("/api/v1/content/videos/2/reparse", json={"index_knowledge": False})
    lifecycle = client.patch("/api/v1/content/knowledge/99/lifecycle", json={"lifecycle_status": "RETIRED", "verification_status": "REJECTED", "note": "人工下线", "operator": "admin"})
    lifecycle_alias = client.patch("/api/v1/content/knowledge-units/99/lifecycle", json={"lifecycle_status": "ACTIVE", "verification_status": "VERIFIED"})
    lifecycle_audits = client.get("/api/v1/content/knowledge-units/99/lifecycle-audits")
    lifecycle_sweep = client.post("/api/v1/content/knowledge/lifecycle/sweep", json={"limit": 10})
    conflicts = client.get("/api/v1/content/knowledge/conflicts", params={"subject_key": "券商"})
    current = client.get("/api/v1/content/knowledge/subjects/券商/current", params={"domain": "COMPANY"})
    history = client.get("/api/v1/content/knowledge/subjects/券商/history")
    frame = client.get("/api/v1/content/videos/2/frames/1/image")
    frame_by_filename = client.get("/api/v1/content/video-frames/BVTEST123/BVTEST123_000001.jpg")
    assert task.status_code == 200
    assert videos.status_code == 200
    assert video.status_code == 200
    assert document.status_code == 200
    assert deleted.status_code == 200
    assert segments.status_code == 200
    assert events.status_code == 200
    assert chapters.status_code == 200
    assert chapter.status_code == 200
    assert knowledge.status_code == 200
    assert knowledge_alias.status_code == 200
    assert unit.status_code == 200
    assert unit_alias.status_code == 200
    assert search.status_code == 200
    assert reparse.status_code == 200
    assert lifecycle.status_code == 200
    assert lifecycle_alias.status_code == 200
    assert lifecycle_audits.status_code == 200
    assert lifecycle_sweep.status_code == 200
    assert conflicts.status_code == 200
    assert current.status_code == 200
    assert history.status_code == 200
    assert frame.status_code == 200
    assert frame_by_filename.status_code == 200
    assert task.json()["stage"] == "asr"
    assert videos.json()["items"][0]["bvid"] == "BVTEST123"
    assert videos.json()["limit"] == 50
    assert videos.json()["next_cursor"] is None
    assert video.json()["video"]["title"] == "测试视频"
    assert document.json()["content"].startswith("# 测试视频")
    assert deleted.json()["deleted"] is True
    assert segments.json()["segments"][0]["text"] == "测试"
    assert events.json()["events"][0]["event_type"] == "OPINION"
    assert chapters.json()["chapters"][0]["title"] == "章节"
    assert chapters.json()["items"][0]["title"] == "章节"
    assert chapters.json()["limit"] == 200
    assert knowledge.json()["items"][0]["subject_key"] == "券商"
    assert knowledge.json()["next_cursor"] is None
    assert knowledge.json()["filters"]["subject_key"] == "券商"
    assert unit.json()["canonical_statement"] == "知识"
    assert unit_alias.json()["canonical_statement"] == "知识"
    assert search.json()["limit"] == 3
    assert reparse.json()["task"]["task_id"] == 8
    assert lifecycle.json()["lifecycle_status"] == "RETIRED"
    assert lifecycle.json()["operator"] == "admin"
    assert lifecycle_alias.json()["lifecycle_status"] == "ACTIVE"
    assert lifecycle_audits.json()["items"][0]["to_lifecycle_status"] == "RETIRED"
    assert lifecycle_sweep.json()["expired_count"] == 1
    assert conflicts.json()["items"][0]["conflict_group_id"] == "cg1"
    assert current.json()["domain"] == "COMPANY"
    assert current.json()["filters"]["subject_key"] == "券商"
    assert history.json()["subject_key"] == "券商"


def test_content_video_knowledge_contract_validation(monkeypatch):
    monkeypatch.setattr("app.api.content_ingest_service", FakeContentService())
    oversized = client.get("/api/v1/content/videos", params={"limit": 999})
    invalid_kind = client.get("/api/v1/content/videos/2/knowledge", params={"knowledge_kind": "unknown"})
    invalid_search = client.post("/api/v1/content/knowledge/search", json={"query": "券商", "filters": {"lifecycle_status": "bad"}})

    assert oversized.status_code == 200
    assert oversized.json()["limit"] == 200
    assert "limit_clamped_to_200" in oversized.json()["warnings"]
    assert invalid_kind.status_code == 400
    assert invalid_kind.json()["detail"] == "invalid knowledge_kind: unknown"
    assert invalid_search.status_code == 400
    assert invalid_search.json()["detail"] == "invalid lifecycle_status: bad"


def test_content_summarize_api(monkeypatch):
    monkeypatch.setattr("app.api.content_ingest_service", FakeContentService())
    response = client.post("/api/v1/content/bilibili/summarize", json={"url": "https://www.bilibili.com/video/BVTEST123"})
    assert response.status_code == 200
    assert response.json()["summary"]["core_summary"] == "摘要"


def test_xiaoe_hls_ingest_requires_authorized_content(monkeypatch):
    monkeypatch.setattr("app.api.content_ingest_service", FakeContentService())
    response = client.post("/api/v1/content/xiaoe/hls/ingest", json={"m3u8_url": "https://media.example.com/v_abc/index.m3u8"})
    assert response.status_code == 400


def test_xiaoe_hls_summarize_api(monkeypatch):
    monkeypatch.setattr("app.api.content_ingest_service", FakeContentService())
    response = client.post(
        "/api/v1/content/xiaoe/hls/summarize",
        json={
            "m3u8_url": "https://media.example.com/v_abc/index.m3u8",
            "authorized_content": True,
            "title": "小鹅通视频",
        },
    )
    assert response.status_code == 200
    assert response.json()["video"]["id"] == 4
    assert response.json()["summary"]["core_summary"] == "摘要"


def test_content_task_options_redact_xiaoe_hls_secrets():
    options = ContentTaskRepository._redact_sensitive_options(
        {
            "m3u8_url": "https://media.example.com/index.m3u8?token=secret",
            "headers": {
                "Cookie": "session=secret",
                "Authorization": "Bearer secret",
                "Referer": "https://appaoswidcd4711.h5.xiaoeknow.com/",
            },
        }
    )
    assert options["m3u8_url"] == "https://media.example.com/index.m3u8?<redacted>"
    assert options["headers"]["Cookie"] == "<redacted>"
    assert options["headers"]["Authorization"] == "<redacted>"
    assert options["headers"]["Referer"].startswith("https://")


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
