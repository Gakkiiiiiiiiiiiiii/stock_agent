"""前向模拟盘 worker 测试：幂等、落库结构、记账推进、重挖开关。"""
import json
from datetime import timedelta

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
    monkeypatch.setattr(fpw, "next_trading_day", lambda day: day + timedelta(days=1))
    lib = tmp_path / "lib.yaml"
    lib.write_text(
        "factors:\n"
        "- id: F001\n"
        "  rpn: [ret, cs_rank]\n"
        "  expression: 'ret cs_rank'\n"
        "  hypothesis: 动量\n"
        "  metrics: {fitness: 1.0}\n"
        "  status: ACTIVE\n",
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
    generated = fpw.generate_orders(
        execution_date="2026-07-30",
        state_dir=env["state"], library_path=env["lib"],
        panel_loader=env["make_loader"](29), remine_days=9999,
    )
    assert generated["skipped"] is False
    result = fpw.run_daily(
        state_dir=env["state"], library_path=env["lib"],
        panel_loader=env["make_loader"](30), remine_days=9999,
    )
    assert result["skipped"] is False
    assert result["top_k"] == 5  # max(5, 8*1%)
    signal_payload = json.loads((env["state"] / "signals_2026-07-29.json").read_text(encoding="utf-8"))
    order_payload = json.loads((env["state"] / "orders_2026-07-30.json").read_text(encoding="utf-8"))
    assert signal_payload["signal_date"] == "2026-07-29"
    assert order_payload["signal_date"] == "2026-07-29"
    assert order_payload["execution_date"] == "2026-07-30"
    assert len(order_payload["picks"]) == 5
    assert order_payload["picks"][0]["symbol"] == "600007.SH"  # T-1 ret 最高
    assert order_payload["picks"][0]["rank"] == 1
    assert order_payload["picks"][0]["alpha_score"] == pytest.approx(1.0)
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
    fpw.generate_orders(execution_date="2026-07-30", state_dir=env["state"], library_path=env["lib"], panel_loader=env["make_loader"](29), remine_days=9999)
    first = fpw.run_daily(**kwargs)
    second = fpw.run_daily(**kwargs)
    assert second["skipped"] is True
    # orders 文件未被重写
    payload = json.loads((env["state"] / "orders_2026-07-30.json").read_text(encoding="utf-8"))
    assert payload["generated_at"]
    lines = (env["state"] / "equity.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert first["orders_file"] == second["orders_file"]


def test_force_regenerates_but_does_not_double_book(env):
    kwargs = dict(state_dir=env["state"], library_path=env["lib"],
                  panel_loader=env["make_loader"](30), remine_days=9999)
    fpw.generate_orders(execution_date="2026-07-30", state_dir=env["state"], library_path=env["lib"], panel_loader=env["make_loader"](29), remine_days=9999)
    fpw.run_daily(**kwargs)
    forced = fpw.run_daily(force=True, **kwargs)
    assert forced["skipped"] is True
    assert forced["bookkeeping"]["advanced"] is False  # 当日已记账，不重复记账
    lines = (env["state"] / "equity.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


def test_bookkeeping_advances_next_day(env):
    fpw.generate_orders(execution_date="2026-07-30", state_dir=env["state"], library_path=env["lib"], panel_loader=env["make_loader"](29), remine_days=9999)
    fpw.run_daily(state_dir=env["state"], library_path=env["lib"],
                  panel_loader=env["make_loader"](30), remine_days=9999)
    # 次日面板多一个交易日
    fpw.generate_orders(execution_date="2026-07-31", state_dir=env["state"], library_path=env["lib"], panel_loader=env["make_loader"](30), remine_days=9999)
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


def test_t_day_signal_not_used_at_t_open(env):
    panel = _panel()
    mutated = {key: value.copy() for key, value in panel.items()}
    mutated["ret"][0, -1] = 99.0
    mutated["close"][0, -1] *= 10

    def loader(symbols, days):
        return {k: v[:, :30] for k, v in mutated.items()}, _dates(30), list(symbols), None

    fpw.generate_orders(execution_date="2026-07-30", state_dir=env["state"], library_path=env["lib"], panel_loader=env["make_loader"](29), remine_days=9999)
    fpw.run_daily(state_dir=env["state"], library_path=env["lib"], panel_loader=loader, remine_days=9999)
    order_payload = json.loads((env["state"] / "orders_2026-07-30.json").read_text(encoding="utf-8"))
    assert order_payload["execution_date"] == "2026-07-30"
    assert order_payload["picks"][0]["symbol"] == "600007.SH"
    assert "600000.SH" not in [item["symbol"] for item in order_payload["picks"]]


def test_generate_orders_rejects_non_next_trading_day(env):
    class WarningMiner:
        def __init__(self, model_client=None):
            pass

        def mine(self, panel, symbols, **kwargs):
            return {"accepted": [], "rejected": [], "warning": "挖掘模型不可用",
                    "stopped_early": False, "stop_reason": None, "evaluated": 0}

    result = fpw.generate_orders(
        execution_date="2026-07-30",
        state_dir=env["state"], library_path=env["lib"],
        panel_loader=env["make_loader"](30), remine_days=0,
        miner_factory=WarningMiner,
    )
    assert result["warning"] and "下一交易日" in result["warning"]


def test_remine_warning_does_not_block(env):
    class WarningMiner:
        def __init__(self, model_client=None):
            pass

        def mine(self, panel, symbols, **kwargs):
            return {"accepted": [], "rejected": [], "warning": "挖掘模型不可用",
                    "stopped_early": False, "stop_reason": None, "evaluated": 0}

    result = fpw.generate_orders(
        execution_date="2026-07-30",
        state_dir=env["state"], library_path=env["lib"],
        panel_loader=env["make_loader"](29), remine_days=0,
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

    fpw.generate_orders(
        execution_date="2026-07-30",
        state_dir=env["state"], library_path=env["lib"],
        panel_loader=env["make_loader"](29), remine_days=5,
        miner_factory=OkMiner,
    )
    assert calls
    remine = json.loads((env["state"] / "remine_state.json").read_text(encoding="utf-8"))
    assert remine["last_remine_date"] == "2026-07-29"
    # 次日（距上次挖掘仅 1 个交易日 < 5）不再触发
    fpw.generate_orders(execution_date="2026-07-31", state_dir=env["state"], library_path=env["lib"],
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


def test_execution_skips_when_open_orders_missing(env):
    result = fpw.run_daily(state_dir=env["state"], library_path=env["lib"], panel_loader=env["make_loader"](30), remine_days=9999)
    assert result["skipped"] is True
    assert "订单不存在" in result["warning"]


def test_frozen_order_cannot_be_overwritten_with_force(env):
    first = fpw.generate_orders(execution_date="2026-07-30", state_dir=env["state"], library_path=env["lib"], panel_loader=env["make_loader"](29), remine_days=9999)
    second = fpw.generate_orders(execution_date="2026-07-30", state_dir=env["state"], library_path=env["lib"], panel_loader=env["make_loader"](29), force=True, remine_days=9999)
    assert first["skipped"] is False
    assert second["skipped"] is True


def test_invalid_frozen_order_metadata_blocks_execution(env):
    fpw.generate_orders(execution_date="2026-07-30", state_dir=env["state"], library_path=env["lib"], panel_loader=env["make_loader"](29), remine_days=9999)
    path = env["state"] / "orders_2026-07-30.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["generated_at"] = "2026-07-30T09:31:00+08:00"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = fpw.run_daily(state_dir=env["state"], library_path=env["lib"], panel_loader=env["make_loader"](30), remine_days=9999)
    assert result["skipped"] is True
    assert "冻结截止" in result["warning"]


def test_cli_exit_code_zero(env, monkeypatch, capsys):
    monkeypatch.setattr(fpw, "load_universe", lambda: list(SYMBOLS))
    monkeypatch.setattr(fpw, "load_factor_panel",
                        lambda symbols, days: ({}, [], [], "QMT 不可达"))
    code = fpw.main(["--state-dir", str(env["state"])])
    assert code == 0
