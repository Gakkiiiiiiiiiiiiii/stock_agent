from __future__ import annotations

from mcp_servers import content_server


class FakeContentService:
    def enqueue_bilibili(self, **kwargs):
        assert kwargs["bv_id"] == "BVTEST123"
        return {"task_id": 5, "status": "pending"}

    def get_video_detail(self, video_id, summary_mode="investment"):
        assert video_id == 9
        assert summary_mode == "investment"
        return {"video": {"id": 9}, "summary": {"core_summary": "视频摘要"}, "segments": []}

    def get_video_segments(self, video_id):
        assert video_id == 9
        return {"video_id": 9, "segments": [{"text": "片段"}]}


def test_ingest_bilibili_video_tool(monkeypatch):
    monkeypatch.setattr("mcp_servers.content_server.service", FakeContentService())
    result = content_server.ingest_bilibili_video(bv_id="BVTEST123")
    assert result["task_id"] == 5


def test_get_video_summary_tool(monkeypatch):
    monkeypatch.setattr("mcp_servers.content_server.service", FakeContentService())
    result = content_server.get_video_summary(9)
    assert result["found"] is True
    assert result["summary"]["core_summary"] == "视频摘要"


def test_search_video_insights_uses_bilibili_source_filter(monkeypatch):
    monkeypatch.setattr(
        "mcp_servers.content_server.retrieve_memory",
        lambda query, filters, top_k: {"query": query, "filters": filters, "top_k": top_k},
    )
    result = content_server.search_video_insights("黄金", top_k=3, themes=["黄金"])
    assert result["filters"]["source_type"] == "bilibili_video_summary"
    assert result["filters"]["related_theme"] == ["黄金"]
