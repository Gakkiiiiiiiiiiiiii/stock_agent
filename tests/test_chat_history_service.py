from pathlib import Path

from app.chat_history_service import ChatHistoryService


def test_chat_history_service_roundtrip(tmp_path: Path):
    service = ChatHistoryService(root=tmp_path)
    created = service.create_session("历史会话")
    session_id = created["session_id"]

    service.save_turn(session_id=session_id, user_query="明天看什么方向", assistant_content="先看黄金和高股息。", response={"report": "先看黄金和高股息。"})

    loaded = service.get_session(session_id)
    assert loaded["title"] == "历史会话"
    assert len(loaded["messages"]) == 2
    assert loaded["messages"][0]["content"] == "明天看什么方向"
    assert loaded["messages"][1]["content"] == "先看黄金和高股息。"
    assert service.list_sessions()[0]["session_id"] == session_id
