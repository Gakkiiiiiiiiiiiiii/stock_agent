"""walk-forward 滚动重挖预检测试：无前视、输出结构、命中率计算。"""
import json

import numpy as np
import pytest

from engines.factor.miner import FactorMiner
from engines.factor.walkforward import DISCLAIMER, run_walkforward


class FakeModelClient:
    """固定返回一个动量候选因子的假模型客户端（同 test_factor_miner 模式）。"""

    model = "fake-model"

    def __init__(self, candidates: list[dict]):
        self._payload = json.dumps(candidates, ensure_ascii=False)
        self.prompts: list[str] = []

    def available(self) -> bool:
        return True

    def complete(self, prompt, system=None, temperature=0.2):
        self.prompts.append(prompt)
        return {"content": self._payload}


@pytest.fixture(autouse=True)
def _neutralize_seed_library(monkeypatch):
    """真实 Alpha191 种子库参与判重，会与单调趋势面板上的动量因子高相关；
    测试里替换为空 rpn 种子（VM 不可计算即跳过），避免干扰入库。"""
    monkeypatch.setattr(
        "engines.factor.miner._load_seed_entries",
        lambda: [{"name": "noop", "hypothesis": "无关种子", "rpn": []}],
    )


def _trend_panel(n_symbols: int = 12, n_days: int = 120):
    """恒定漂移面板：标的序号越大日收益越高。

    ret 截面与未来收益严格单调（rank_ic≈1，挖掘必入库），且任意时点 TopK
    都是漂移最高的尾部标的，之后各窗口必然跑赢等权基准。
    """
    drift = np.linspace(0.0002, 0.002, n_symbols)
    returns = np.repeat(drift[:, None], n_days, axis=1)
    close = 100 * np.cumprod(1 + returns, axis=1)
    volume = np.full_like(close, 1e6)
    amount = close * volume
    return {
        "open": close, "high": close, "low": close, "close": close,
        "volume": volume, "amount": amount,
        "turnover": np.full_like(close, 0.01),
        "vwap": close, "ret": returns,
    }, [f"S{i:04d}" for i in range(n_symbols)], \
        [f"2026-{(d // 30) + 1:02d}-{(d % 30) + 1:02d}" for d in range(n_days)]


def _run(panel, dates, symbols, **kwargs):
    client = FakeModelClient([{"rpn": ["ret", "cs_rank"], "hypothesis": "趋势延续"}])
    params = dict(
        rebalance_points=[60, 80], rounds=1, candidates_per_round=1,
        horizon=5, top_k=3, model_client=client,
    )
    params.update(kwargs)
    return run_walkforward(panel, dates, symbols, **params)


def test_walkforward_output_structure():
    panel, symbols, dates = _trend_panel()
    result = _run(panel, dates, symbols)
    assert result["warning"] is None
    assert result["disclaimer"] == DISCLAIMER
    assert "前向模拟盘" in result["disclaimer"]
    assert len(result["per_window"]) == 2
    # 每个窗口 5 个记账日（T+1 起），两窗口共 10 天
    assert len(result["dates"]) == 10
    assert len(result["equity_curve"]) == len(result["benchmark_curve"]) == 10
    metrics = result["metrics"]
    for key in ("total_return", "sharpe", "max_drawdown", "excess_annual_return", "excess_sharpe"):
        assert key in metrics


def test_walkforward_window_hit_rate():
    """趋势面板上两个窗口 TopK 都应跑赢等权基准 → hit_rate = 1.0。"""
    panel, symbols, dates = _trend_panel()
    result = _run(panel, dates, symbols)
    hits = [w["hit"] for w in result["per_window"]]
    assert result["window_hit_rate"] == pytest.approx(sum(hits) / len(hits))
    assert result["window_hit_rate"] == 1.0
    for w in result["per_window"]:
        assert w["excess_return"] > 0
        # TopK=3，应是漂移最高的三只标的
        assert w["picks"] == ["S0011", "S0010", "S0009"]


def test_walkforward_library_snapshot_inherited():
    """第二个调仓点的挖掘库从首点快照继承：同一公式被判重，但 active 因子仍保留。"""
    panel, symbols, dates = _trend_panel()
    result = _run(panel, dates, symbols)
    first, second = result["per_window"]
    assert first["factor_count"] == 1 and first["accepted_count"] == 1
    assert second["factor_count"] == 1  # 继承首点快照
    assert second["factor_ids"] == first["factor_ids"]


def test_walkforward_no_lookahead():
    """改动 T 之后的数据不影响 T 时点的组池与因子快照（但会改变记账净值）。"""
    panel, symbols, dates = _trend_panel()
    base = _run(panel, dates, symbols)

    mutated = {k: v.copy() for k, v in panel.items()}
    # 篡改第二个调仓点（T=80）之后的 close/ret，使未来收益结构完全改变
    mutated["close"][:, 82:] *= np.linspace(1.0, 0.5, 120 - 82)[None, ::-1]
    mutated["ret"][:, 82:] = -mutated["ret"][:, 82:]
    alt = _run(mutated, dates, symbols)

    for w_base, w_alt in zip(base["per_window"], alt["per_window"]):
        assert w_base["picks"] == w_alt["picks"]
        assert w_base["factor_ids"] == w_alt["factor_ids"]
        assert w_base["factor_count"] == w_alt["factor_count"]
    # 记账区间使用 >T 的数据，净值理应变化
    assert base["equity_curve"] != alt["equity_curve"]


def test_walkforward_rejects_insufficient_sample():
    panel, symbols, dates = _trend_panel()
    result = _run(panel, dates, symbols, rebalance_points=[119])  # 119 无 T+1 可用
    assert result["equity_curve"] == []
    assert "无可用调仓点" in result["warning"]


def test_walkforward_empty_panel():
    result = run_walkforward({}, [], [])
    assert result["warning"]
    assert result["disclaimer"] == DISCLAIMER


def test_walkforward_graceful_when_model_unavailable():
    """LLM 不可用时不报错：无因子可合成，组合持有现金，窗口照常滚动。"""
    panel, symbols, dates = _trend_panel()
    client = FakeModelClient([])
    client.available = lambda: False
    result = run_walkforward(
        panel, dates, symbols,
        rebalance_points=[60], rounds=1, candidates_per_round=1,
        horizon=5, top_k=3, model_client=client,
    )
    assert result["warning"]
    assert result["per_window"][0]["factor_count"] == 0
    assert result["per_window"][0]["picks"] == []
    assert len(result["equity_curve"]) == 5
