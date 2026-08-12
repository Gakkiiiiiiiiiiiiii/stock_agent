from __future__ import annotations

from mcp_servers import content_server


class FakeContentService:
    def enqueue_bilibili(self, **kwargs):
        assert kwargs["bv_id"] == "BVTEST123"
        return {"task_id": 5, "status": "pending"}

    def enqueue_xiaoe_hls(self, **kwargs):
        assert kwargs["m3u8_url"] == "https://media.example.com/v_abc/index.m3u8"
        assert kwargs["authorized_content"] is True
        return {"task_id": 6, "status": "pending"}

    def get_video_summary(self, video_id):
        assert video_id == 9
        return {"video_id": 9, "core_summary": "视频摘要"}

    def get_video_segments(self, video_id):
        assert video_id == 9
        return {"video_id": 9, "segments": [{"text": "片段"}]}

    def search_video_knowledge(self, query, filters=None, limit=5, intent=None):
        return {"query": query, "filters": filters or {}, "limit": limit, "items": [{"statement": "黄金观点"}]}

    def list_video_knowledge_units(self, video_id, filters=None, limit=None):
        return {"video_id": video_id, "items": [{"id": 99, "lifecycle_status": "ACTIVE"}], "filters": filters or {}, "limit": limit}

    def get_knowledge_unit(self, unit_id):
        return {"id": unit_id, "canonical_statement": "知识", "evidence": [{"evidence_text": "证据"}]}

    def list_knowledge_conflicts(self, subject_key=None, limit=50):
        return {"items": [{"conflict_group_id": "cg1", "units": [{"id": 99, "lifecycle_status": "ACTIVE"}]}], "limit": limit, "subject_key": subject_key}

    def get_current_subject_state(self, subject_key, domain=None, limit=20):
        return {"subject_key": subject_key, "domain": domain, "items": [{"subject_key": subject_key, "primary_domain": domain}]}

    def get_subject_history(self, subject_key, domain=None, limit=50):
        return {"subject_key": subject_key, "domain": domain, "items": [{"as_of_time": "2026-07-28T09:30:00", "lifecycle_status": "EXPIRED"}]}


def test_ingest_bilibili_video_tool(monkeypatch):
    monkeypatch.setattr("mcp_servers.content_server.service", FakeContentService())
    result = content_server.ingest_bilibili_video(bv_id="BVTEST123")
    assert result["task_id"] == 5


def test_ingest_xiaoe_hls_video_tool(monkeypatch):
    monkeypatch.setattr("mcp_servers.content_server.service", FakeContentService())
    result = content_server.ingest_xiaoe_hls_video(
        m3u8_url="https://media.example.com/v_abc/index.m3u8",
        authorized_content=True,
    )
    assert result["task_id"] == 6


def test_get_video_summary_tool(monkeypatch):
    monkeypatch.setattr("mcp_servers.content_server.service", FakeContentService())
    result = content_server.get_video_summary(9)
    assert result["found"] is True
    assert result["core_summary"] == "视频摘要"


def test_search_video_insights_uses_video_knowledge(monkeypatch):
    monkeypatch.setattr("mcp_servers.content_server.service", FakeContentService())
    result = content_server.search_video_insights("黄金", top_k=3, themes=["黄金"])
    assert result["deprecated"] is True
    assert result["filters"]["subject"] == ["黄金"]
    assert result["limit"] == 3


def test_video_knowledge_tools(monkeypatch):
    monkeypatch.setattr("mcp_servers.content_server.service", FakeContentService())
    search = content_server.search_video_knowledge("券商", intent="current_state", top_k=2, subject_key="券商", predicate_key="估值")
    current = content_server.get_current_subject_state("券商", domains=["COMPANY"])
    history = content_server.get_subject_history("券商", date_from="2026-07-29", include_expired=False)
    units = content_server.get_video_knowledge_units(9, filters={"subject_key": "券商"}, top_k=3)
    unit = content_server.get_knowledge_unit(99)
    conflicts = content_server.list_knowledge_conflicts("券商", status="ACTIVE")
    assert search["filters"]["subject"] == "券商"
    assert search["filters"]["predicate_key"] == "估值"
    assert search["intent"] == "current_state"
    assert search["limit"] == 2
    assert current["filters"]["subject"] == "券商"
    assert history["filters"]["subject"] == "券商"
    assert history["items"]
    assert units["limit"] == 3
    assert unit["found"] is True
    assert unit["evidence"][0]["evidence_text"] == "证据"
    assert conflicts["items"] == []


def test_video_knowledge_mcp_contract_validation(monkeypatch):
    monkeypatch.setattr("mcp_servers.content_server.service", FakeContentService())
    empty = content_server.search_video_knowledge("", top_k=999)
    invalid = content_server.search_video_knowledge("券商", knowledge_kind="mystery", top_k=999)

    assert empty["error"]["code"] == "EMPTY_QUERY"
    assert empty["items"] == []
    assert invalid["limit"] == 100
    assert invalid["error"]["code"] == "INVALID_FILTER"
    assert "top_k_clamped_to_100" in invalid["warnings"]
