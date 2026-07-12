from mcp_servers.industry_knowledge_server import search_theme_logic
from mcp_servers.technical_factor_server import detect_pattern_signal


def test_search_seed_theme():
    result = search_theme_logic("黄金")
    assert result["found"] is True
    assert "实际利率" in result["core_thesis"]


def test_search_bootstrapped_theme():
    result = search_theme_logic("高股息")
    assert result["found"] is True
    assert result["theme_name"] == "高股息"


def test_search_theme_alias():
    result = search_theme_logic("红利资产")
    assert result["found"] is True
    assert result["theme_name"] == "高股息"


def test_detect_pattern_signal_tool():
    result = detect_pattern_signal("SAMPLE", patterns=["B1"])
    assert result["symbol"] == "SAMPLE"
    assert result["signals"][0]["pattern"] == "B1"
