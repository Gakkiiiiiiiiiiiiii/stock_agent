from app.tool_registry import ClaudeToolRegistry


def test_external_knowledge_write_is_not_a_registered_tool(tmp_path, monkeypatch):
    registry = ClaudeToolRegistry()
    assert registry.execute("upsert_theme_logic", {"theme_name": "测试主题"}) == {"error": "unknown tool: upsert_theme_logic"}


def test_order_and_production_tools_are_not_registered():
    names = set(ClaudeToolRegistry()._tools)
    assert not any(name.startswith(("mine_", "ingest_", "train_")) for name in names)
    assert not names.intersection({"evaluate_factor", "scan_alpha_factors", "place_order", "submit_order", "cancel_order", "upsert_theme_logic"})


def test_tool_timeout_enforced(tmp_path, monkeypatch):
    import time

    from app.tool_policy import PermissionLevel, ToolPolicy

    registry = ClaudeToolRegistry()
    registry._tools["slow"] = ({"name": "slow", "description": "slow", "input_schema": {"type": "object", "properties": {}}}, lambda payload: time.sleep(0.2) or {"ok": True})
    registry._policies["slow"] = ToolPolicy(PermissionLevel.COMPUTE, timeout_seconds=0.01)
    assert registry.execute("slow", {})["error"]["code"] == "TOOL_TIMEOUT"
