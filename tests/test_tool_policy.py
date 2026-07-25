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


def test_confirmation_token_single_use(tmp_path, monkeypatch):
    monkeypatch.setenv("PROPOSAL_STORE_PATH", str(tmp_path / "proposals.jsonl"))
    monkeypatch.setenv("TOOL_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    registry = ClaudeToolRegistry()
    registry._tools["upsert_theme_logic"] = (registry._tools["upsert_theme_logic"][0], lambda payload: {"ok": True})
    payload = {"theme_name": "测试主题"}
    proposal = registry.create_proposal("upsert_theme_logic", payload)
    approved = registry.approve_proposal(proposal["proposal_id"])
    assert registry.execute("upsert_theme_logic", payload | {"confirmation_token": approved["confirmation_token"]}) == {"ok": True}
    result = registry.execute("upsert_theme_logic", payload | {"confirmation_token": approved["confirmation_token"]})
    assert result["error"]["code"] == "CONFIRMATION_INVALID"


def test_tool_timeout_enforced(tmp_path, monkeypatch):
    import time
    from app.tool_policy import PermissionLevel, ToolPolicy

    monkeypatch.setenv("PROPOSAL_STORE_PATH", str(tmp_path / "proposals.jsonl"))
    monkeypatch.setenv("TOOL_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    registry = ClaudeToolRegistry()
    registry._tools["slow"] = ({"name": "slow", "description": "slow", "input_schema": {"type": "object", "properties": {}}}, lambda payload: time.sleep(0.2) or {"ok": True})
    registry._policies["slow"] = ToolPolicy(PermissionLevel.COMPUTE, timeout_seconds=0.01)
    assert registry.execute("slow", {})["error"]["code"] == "TOOL_TIMEOUT"
