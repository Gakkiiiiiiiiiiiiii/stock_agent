"""前向模拟盘 worker 测试：幂等、落库结构、记账推进、重挖开关。"""
import json

import numpy as np
import pytest

import workers.factor_paper_worker as fpw


def _panel(n_symbols: int = 8, n_days: int = 40):
    """全标的温和上涨的面板，ret 截面单调便于 TopK 断言。"""
    drift = np.linspace(0.001, 0.004, n_symbols)
    returns = np.repeat(drift[:, None], n_days, axis=1)
    close = 100 * np.cumprod(1 + returns, axis=1)
    volume = np.full_like(close, 1e6)
    return {
        "open": close, "high": close, "low": close, "close": close,
        "volume": volume, "amount": close * volume,
        "turnover": np.full_like(close, 0.01),
        "vwap": close, "ret": returns,
    }


def _dates(n: int, start_day: int = 1):
    return [f"2026-07-{d:02d}" for d in range(start_day, start_day + n)]


SYMBOLS = [f"60000{i}.SH" for i in range(8)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离状态目录 + 假行情 + 单因子库。"""
    monkeypatch.setattr(fpw, "load_universe", lambda: list(SYMBOLS))
    lib = tmp_path / "lib.yaml"
    lib.write_text(
        "factors:\n"
        "- id: F001\n"
        "  rpn: [ret, cs_rank]\n"
        "  expression: 'ret cs_rank'\n"
        "  hypothesis: 动量\n"
        "  metrics: {fitness: 1.0}\n"
        "  status: active\n",
        encoding="utf-8",
    )
    panel = _panel()

    def make_loader(n_days: int):
        def loader(symbols, days):
            p = {k: v[:, :n_days] for k, v in panel.items()}
            return p, _dates(n_days), list(symbols), None
        return loader

    return {"state": tmp_path / "factor_paper", "lib": str(lib), "make_loader": make_loader}


def test_first_run_writes_positions_and_state(env):
    result = fpw.run_daily(
        state_dir=env["state"], library_path=env["lib"],
        panel_loader=env["make_loader"](30), remine_days=9999,
    )
    assert result["skipped"] is False
    assert result["top_k"] == 5  # max(5, 8*1%)
    payload = json.loads((env["state"] / "positions_2026-07-30.json").read_text(encoding="utf-8"))
    assert payload["date"] == "2026-07-30"
    assert payload["generated_at"] and payload["top_k"] == 5
    assert len(payload["picks"]) == 5
    assert payload["picks"][0]["symbol"] == "600007.SH"  # ret 最高
    assert payload["picks"][0]["rank"] == 1
    assert payload["picks"][0]["alpha_score"] == pytest.approx(1.0)
    state = json.loads((env["state"] / "portfolio_state.json").read_text(encoding="utf-8"))
    assert state["last_date"] == "2026-07-30"
    assert state["cash"] < fpw.INITIAL_CASH  # 已买入
    assert len(state["positions"]) == 5
    lots = state["positions"]["600007.SH"]
    assert lots and lots[0]["buy_date"] == "2026-07-30"
    lines = (env["state"] / "equity.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["date"] == "2026-07-30" and row["equity"] > 0 and "turnover" in row


def test_same_day_idempotent(env):
    kwargs = dict(state_dir=env["state"], library_path=env["lib"],
                  panel_loader=env["make_loader"](30), remine_days=9999)
    first = fpw.run_daily(**kwargs)
    second = fpw.run_daily(**kwargs)
    assert second["skipped"] is True
    assert "跳过" in second["message"]
    # positions 文件未被重写
    payload = json.loads((env["state"] / "positions_2026-07-30.json").read_text(encoding="utf-8"))
    assert payload["generated_at"]
    lines = (env["state"] / "equity.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert first["positions_file"] == second["positions_file"]


def test_force_regenerates_but_does_not_double_book(env):
    kwargs = dict(state_dir=env["state"], library_path=env["lib"],
                  panel_loader=env["make_loader"](30), remine_days=9999)
    fpw.run_daily(**kwargs)
    forced = fpw.run_daily(force=True, **kwargs)
    assert forced["skipped"] is False
    assert forced["bookkeeping"]["advanced"] is False  # 当日已记账，不重复记账
    lines = (env["state"] / "equity.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


def test_bookkeeping_advances_next_day(env):
    fpw.run_daily(state_dir=env["state"], library_path=env["lib"],
                  panel_loader=env["make_loader"](30), remine_days=9999)
    # 次日面板多一个交易日
    result = fpw.run_daily(state_dir=env["state"], library_path=env["lib"],
                           panel_loader=env["make_loader"](31), remine_days=9999)
    assert result["bookkeeping"]["advanced"] is True
    state = json.loads((env["state"] / "portfolio_state.json").read_text(encoding="utf-8"))
    assert state["last_date"] == "2026-07-31"
    lines = (env["state"] / "equity.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    day2 = json.loads(lines[1])
    assert day2["date"] == "2026-07-31"
    assert day2["benchmark"] > fpw.INITIAL_CASH  # 全池上涨，基准前进
    # 全标的上涨且持仓未变，净值应高于首日
    day1 = json.loads(lines[0])
    assert day2["equity"] > day1["equity"]


def test_remine_warning_does_not_block(env):
    class WarningMiner:
        def __init__(self, model_client=None):
            pass

        def mine(self, panel, symbols, **kwargs):
            return {"accepted": [], "rejected": [], "warning": "挖掘模型不可用",
                    "stopped_early": False, "stop_reason": None, "evaluated": 0}

    result = fpw.run_daily(
        state_dir=env["state"], library_path=env["lib"],
        panel_loader=env["make_loader"](30), remine_days=0,
        miner_factory=WarningMiner,
    )
    assert result["skipped"] is False  # 组池照常完成
    assert "重挖跳过" in result["warning"]
    assert not (env["state"] / "remine_state.json").exists()  # 失败不更新，次日重试


def test_remine_success_writes_state(env):
    calls = []

    class OkMiner:
        def __init__(self, model_client=None):
            pass

        def mine(self, panel, symbols, **kwargs):
            calls.append(1)
            return {"accepted": [{"id": "F002"}], "rejected": [], "warning": None,
                    "stopped_early": False, "stop_reason": None, "evaluated": 1}

    fpw.run_daily(
        state_dir=env["state"], library_path=env["lib"],
        panel_loader=env["make_loader"](30), remine_days=5,
        miner_factory=OkMiner,
    )
    assert calls
    remine = json.loads((env["state"] / "remine_state.json").read_text(encoding="utf-8"))
    assert remine["last_remine_date"] == "2026-07-30"
    # 次日（距上次挖掘仅 1 个交易日 < 5）不再触发
    fpw.run_daily(state_dir=env["state"], library_path=env["lib"],
                  panel_loader=env["make_loader"](31), remine_days=5,
                  miner_factory=OkMiner)
    assert len(calls) == 1


def test_qmt_unavailable_graceful(env):
    def loader(symbols, days):
        return {}, [], [], "QMT 行情桥接未返回任何K线数据"

    result = fpw.run_daily(state_dir=env["state"], library_path=env["lib"],
                           panel_loader=loader, remine_days=9999)
    assert result["date"] is None
    assert "QMT" in result["warning"]


def test_cli_exit_code_zero(env, monkeypatch, capsys):
    monkeypatch.setattr(fpw, "load_universe", lambda: list(SYMBOLS))
    monkeypatch.setattr(fpw, "load_factor_panel",
                        lambda symbols, days: ({}, [], [], "QMT 不可达"))
    code = fpw.main(["--state-dir", str(env["state"])])
    assert code == 0
