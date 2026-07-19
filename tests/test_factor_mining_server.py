import numpy as np

from mcp_servers import factor_mining_server, technical_factor_server


def _panel(symbols: list[str], n_days: int = 70):
    n = len(symbols)
    rng = np.random.default_rng(1)
    base = 100 * np.cumprod(1 + rng.normal(0, 0.01, size=(n, n_days)), axis=1)
    volume = np.full_like(base, 1e6)
    panel = {
        "open": base, "high": base, "low": base, "close": base,
        "volume": volume, "amount": base * volume,
        "turnover": np.full_like(base, 0.01),
        "vwap": base, "ret": np.full_like(base, 0.001),
    }
    dates = [f"2026-01-{d + 1:02d}" for d in range(n_days)]
    return panel, dates, symbols, None


def _library():
    return {"factors": [{
        "id": "F001", "rpn": ["close", "cs_rank"], "expression": "close cs_rank",
        "hypothesis": "测试因子", "metrics": {"rank_ic": 0.05, "icir": 0.4, "fitness": 0.6},
        "universe": [], "horizon": 5, "status": "active",
    }]}


def test_scan_alpha_factors_ranks_symbols(monkeypatch):
    symbols = [f"60000{i}.SH" for i in range(5)]
    monkeypatch.setattr(factor_mining_server, "load_factor_panel", lambda s=None, days=250: _panel(symbols))
    monkeypatch.setattr(factor_mining_server, "load_library", lambda path=None: _library())
    result = factor_mining_server.scan_alpha_factors(symbols)
    assert result["factor_count"] == 1
    assert len(result["items"]) == len(symbols)
    ranks = sorted(item["alpha_rank"] for item in result["items"])
    assert ranks == [1, 2, 3, 4, 5]
    assert all("alpha_score" in item for item in result["items"])
    assert result["disclaimer"]


def test_scan_alpha_factors_empty_library(monkeypatch):
    monkeypatch.setattr(factor_mining_server, "load_library", lambda path=None: {"factors": []})
    result = factor_mining_server.scan_alpha_factors(["600000.SH"])
    assert result["items"] == []
    assert result["warning"]


def test_scan_alpha_factors_skips_nan_symbols(monkeypatch):
    symbols = ["600000.SH", "600001.SH", "600002.SH"]
    panel, dates, syms, warn = _panel(symbols)
    # 第二只票全部 NaN（如停牌/缺数据），不应挤占名次
    panel["close"][1, :] = np.nan
    monkeypatch.setattr(factor_mining_server, "load_factor_panel", lambda s=None, days=250: (panel, dates, syms, warn))
    monkeypatch.setattr(factor_mining_server, "load_library", lambda path=None: _library())
    result = factor_mining_server.scan_alpha_factors(symbols)
    assert len(result["items"]) == 2
    assert [item["alpha_rank"] for item in result["items"]] == [1, 2]
    assert "600001.SH" not in [item["symbol"] for item in result["items"]]


def test_scan_stock_signals_appends_alpha_top(monkeypatch):
    symbols = [f"60000{i}.SH" for i in range(10)]

    def fake_detect(symbol, date=None, patterns=None):
        return {"symbol": symbol, "date": "2026-07-17", "signals": []}

    def fake_scan(symbols_arg=None):
        return {
            "items": [
                {"symbol": s, "alpha_score": 1.0 - i * 0.01, "alpha_rank": i + 1, "factor_count": 2}
                for i, s in enumerate(symbols_arg)
            ],
        }

    monkeypatch.setattr(technical_factor_server, "detect_pattern_signal", fake_detect)
    monkeypatch.setattr(factor_mining_server, "scan_alpha_factors", fake_scan)

    result = technical_factor_server.scan_stock_signals(symbols)
    items = result["items"]
    assert all("alpha_rank" in item for item in items)
    top_item = next(item for item in items if item["symbol"] == "600000.SH")
    alpha_signals = [s for s in top_item["signals"] if s["pattern"] == "ALPHA_TOP"]
    assert len(alpha_signals) == 1  # top 10%（10 只 → 第 1 名）触发
    assert alpha_signals[0]["triggered"] is True
    assert 0 <= alpha_signals[0]["score"] <= 100
    # 非 top 标的不追加信号
    other = next(item for item in items if item["symbol"] == "600005.SH")
    assert [s for s in other["signals"] if s["pattern"] == "ALPHA_TOP"] == []


def test_scan_stock_signals_tolerates_factor_failure(monkeypatch):
    def fake_detect(symbol, date=None, patterns=None):
        return {"symbol": symbol, "date": "2026-07-17", "signals": []}

    def boom(symbols_arg=None):
        raise RuntimeError("factor engine down")

    monkeypatch.setattr(technical_factor_server, "detect_pattern_signal", fake_detect)
    monkeypatch.setattr(factor_mining_server, "scan_alpha_factors", boom)
    result = technical_factor_server.scan_stock_signals(["600000.SH"])
    assert result["items"][0]["symbol"] == "600000.SH"  # 原逻辑不受影响
