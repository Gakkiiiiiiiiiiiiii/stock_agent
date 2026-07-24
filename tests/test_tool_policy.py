from app.tool_registry import ClaudeToolRegistry


def test_confirmed_write_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("PROPOSAL_STORE_PATH", str(tmp_path / "proposals.jsonl"))
    monkeypatch.setenv("TOOL_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    registry = ClaudeToolRegistry()
    result = registry.execute("upsert_theme_logic", {"theme_name": "测试主题"})
    assert result["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_confirmed_write_with_approved_token(tmp_path, monkeypatch):
    monkeypatch.setenv("PROPOSAL_STORE_PATH", str(tmp_path / "proposals.jsonl"))
    monkeypatch.setenv("TOOL_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    registry = ClaudeToolRegistry()

    calls = []
    registry._tools["upsert_theme_logic"] = (registry._tools["upsert_theme_logic"][0], lambda payload: calls.append(payload) or {"ok": True})
    payload = {"theme_name": "测试主题"}
    proposal = registry.create_proposal("upsert_theme_logic", payload)
    approved = registry.approve_proposal(proposal["proposal_id"])
    result = registry.execute("upsert_theme_logic", payload | {"confirmation_token": approved["confirmation_token"]})
    assert result == {"ok": True}
    assert calls == [payload]
