import json

import numpy as np

from engines.factor.miner import FactorMiner


class FakeModelClient:
    model = "fake-model"

    def __init__(self, candidates: list[dict]):
        self._payload = json.dumps(candidates, ensure_ascii=False)
        self.prompts: list[str] = []

    def available(self) -> bool:
        return True

    def complete(self, prompt, system=None, temperature=0.2):
        self.prompts.append(prompt)
        return {"content": self._payload}


def _panel(n_symbols: int = 20, n_days: int = 80, seed: int = 3):
    """构造动量自相关数据：ret[t] 与 ret[t-5] 正相关，使 ["ret","cs_rank"] 在 horizon=5 下有效。"""
    rng = np.random.default_rng(seed)
    shock = rng.normal(0, 0.02, size=(n_symbols, n_days))
    returns = np.zeros_like(shock)
    returns[:, :] = shock
    returns[:, 5:] += 0.6 * shock[:, :-5]  # 5 日动量自相关
    close = 100 * np.cumprod(1 + returns, axis=1)
    volume = np.full_like(close, 1e6)
    amount = close * volume
    return {
        "open": close, "high": close, "low": close, "close": close,
        "volume": volume, "amount": amount,
        "turnover": np.full_like(close, 0.01),
        "vwap": close, "ret": returns,
    }, [f"S{i:04d}" for i in range(n_symbols)]


def _good_candidate():
    return {"rpn": ["ret", "cs_rank"], "hypothesis": "5日动量：短期强势延续"}


def test_miner_accepts_passing_factor(tmp_path):
    panel, symbols = _panel()
    client = FakeModelClient([_good_candidate()])
    miner = FactorMiner(model_client=client, library_path=str(tmp_path / "lib.yaml"))
    result = miner.mine(panel, symbols, rounds=1, candidates_per_round=1, horizon=5)
    assert result["warning"] is None
    assert len(result["accepted"]) == 1
    entry = result["accepted"][0]
    assert entry["id"] == "F001"
    assert entry["metrics"]["rank_ic"] > 0.02
    assert entry["metrics"]["passed"] is True


def test_miner_rejects_invalid_formula(tmp_path):
    panel, symbols = _panel()
    client = FakeModelClient([
        {"rpn": ["ret", "bogus_op"], "hypothesis": "非法 token"},
        {"rpn": ["ret"], "hypothesis": "缺少横截面收尾"},
        {"hypothesis": "缺少 rpn"},
    ])
    miner = FactorMiner(model_client=client, library_path=str(tmp_path / "lib.yaml"))
    result = miner.mine(panel, symbols, rounds=1, candidates_per_round=3, horizon=5)
    assert result["accepted"] == []
    assert len(result["rejected"]) == 3


def test_miner_dedupes_against_library(tmp_path):
    panel, symbols = _panel()
    lib = str(tmp_path / "lib.yaml")
    first = FactorMiner(model_client=FakeModelClient([_good_candidate()]), library_path=lib)
    assert len(first.mine(panel, symbols, rounds=1, candidates_per_round=1, horizon=5)["accepted"]) == 1
    # 第二轮提交相同公式 → 判重拒绝
    second = FactorMiner(model_client=FakeModelClient([_good_candidate()]), library_path=lib)
    result = second.mine(panel, symbols, rounds=1, candidates_per_round=1, horizon=5)
    assert result["accepted"] == []
    assert result["rejected"][0]["reason"] == "重复"


def test_miner_prompt_contains_feedback(tmp_path):
    panel, symbols = _panel()
    client = FakeModelClient([{"rpn": ["ret", "bogus_op"], "hypothesis": "x"}])
    miner = FactorMiner(model_client=client, library_path=str(tmp_path / "lib.yaml"))
    miner.mine(panel, symbols, rounds=2, candidates_per_round=1, horizon=5)
    assert len(client.prompts) == 2
    assert "公式非法" in client.prompts[1]  # 上轮反馈进入下一轮 prompt


def test_miner_returns_warning_when_model_unavailable(tmp_path):
    panel, symbols = _panel()
    client = FakeModelClient([])
    client.available = lambda: False
    miner = FactorMiner(model_client=client, library_path=str(tmp_path / "lib.yaml"))
    result = miner.mine(panel, symbols, rounds=1, candidates_per_round=1, horizon=5)
    assert result["warning"]
    assert result["accepted"] == []
