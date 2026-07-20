import json

import numpy as np

from engines.factor import fitness as fitness_mod
from engines.factor.miner import FactorMiner, _rank_ic_threshold


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


class SequenceFakeModelClient:
    """按轮次返回不同候选列表的假模型客户端。"""

    model = "fake-model"

    def __init__(self, payloads: list[list[dict]]):
        self._payloads = [json.dumps(p, ensure_ascii=False) for p in payloads]
        self.prompts: list[str] = []

    def available(self) -> bool:
        return True

    def complete(self, prompt, system=None, temperature=0.2):
        self.prompts.append(prompt)
        index = min(len(self.prompts), len(self._payloads)) - 1
        return {"content": self._payloads[index]}


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


def test_miner_early_stop_on_saturation(tmp_path):
    """第 1 轮入库，之后连续 2 轮 accepted=0 且判重率 1.0 → 饱和早停。"""
    panel, symbols = _panel()
    client = FakeModelClient([_good_candidate()])  # 每轮返回相同公式
    miner = FactorMiner(model_client=client, library_path=str(tmp_path / "lib.yaml"))
    result = miner.mine(panel, symbols, rounds=5, candidates_per_round=1, horizon=5)
    assert result["stopped_early"] is True
    assert "饱和" in result["stop_reason"]
    assert len(client.prompts) == 3  # 第 3 轮结束后即终止，不跑满 5 轮
    assert result["evaluated"] == 2  # 第 3 轮命中拒绝缓存，不再评估


def test_miner_respects_max_candidates_budget(tmp_path, monkeypatch):
    """评估候选数达到预算上限后硬截断。"""
    monkeypatch.setenv("FACTOR_MINING_MAX_CANDIDATES", "2")
    panel, symbols = _panel()
    payloads = [
        [{"rpn": ["volume", "cs_rank"], "hypothesis": "a"}],
        [{"rpn": ["amount", "cs_rank"], "hypothesis": "b"}],
        [{"rpn": ["turnover", "cs_rank"], "hypothesis": "c"}],
    ]
    client = SequenceFakeModelClient(payloads)
    miner = FactorMiner(model_client=client, library_path=str(tmp_path / "lib.yaml"))
    result = miner.mine(panel, symbols, rounds=5, candidates_per_round=1, horizon=5)
    assert result["stopped_early"] is True
    assert "预算" in result["stop_reason"]
    assert result["evaluated"] == 2
    assert len(client.prompts) == 3


def test_miner_caches_rejected_rpn(tmp_path, monkeypatch):
    """前轮评估过但被拒绝的公式，后续轮次直接判重复且不再打分。"""
    panel, symbols = _panel()
    calls: list[int] = []
    real_evaluate = fitness_mod.evaluate_factor

    def counting_evaluate(*args, **kwargs):
        calls.append(1)
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(fitness_mod, "evaluate_factor", counting_evaluate)
    # 用与种子库低相关、且必然不达门槛的价格均线因子
    # （常量类因子会与种子 turnover_mean_20d 面板全等，会先被判重拦截，无法验证打分缓存）
    client = FakeModelClient([{"rpn": ["close", "ts_mean_5", "cs_rank"], "hypothesis": "价格均线"}])
    miner = FactorMiner(model_client=client, library_path=str(tmp_path / "lib.yaml"))
    result = miner.mine(panel, symbols, rounds=2, candidates_per_round=1, horizon=5)
    assert len(calls) == 1  # 第 2 轮命中缓存，未重复打分
    assert result["evaluated"] == 1
    assert result["rejected"][0]["reason"] == "未达门槛"
    assert result["rejected"][1]["reason"] == "重复"


def test_rank_ic_threshold_tightening():
    assert _rank_ic_threshold(30) == fitness_mod.RANK_IC_THRESHOLD
    assert _rank_ic_threshold(31) == fitness_mod.RANK_IC_THRESHOLD * 1.5


def test_miner_tightens_threshold_after_many_evaluations(tmp_path, monkeypatch):
    """累计评估超过 30 个候选后，rank_ic=0.025（过基础门槛 0.02、不过收紧门槛 0.03）被拒绝。"""
    monkeypatch.setenv("FACTOR_MINING_MAX_CANDIDATES", "100")
    panel, symbols = _panel()
    fake_metrics = {
        "rank_ic": 0.025, "ic_mean": 0.02, "icir": 0.5,
        "topk_annual_return": 0.1, "topk_max_drawdown": 0.05,
        "coverage": 1.0, "fitness": 1.0, "top_k": 5, "passed": True,
    }
    monkeypatch.setattr(fitness_mod, "evaluate_factor", lambda *a, **k: dict(fake_metrics))
    monkeypatch.setattr("engines.factor.miner.is_duplicate", lambda *a, **k: False)
    # 32 轮、每轮一个互不相同的合法公式（特征 × 窗口组合）
    windows = [3, 5, 10, 20, 60]
    feats = ["close", "volume", "amount", "high", "low", "open", "vwap"]
    payloads = []
    for i in range(32):
        feat = feats[i % len(feats)]
        window = windows[(i // len(feats)) % len(windows)]
        payloads.append([{"rpn": [feat, f"ts_mean_{window}", "cs_rank"], "hypothesis": f"c{i}"}])
    client = SequenceFakeModelClient(payloads)
    miner = FactorMiner(model_client=client, library_path=str(tmp_path / "lib.yaml"))
    result = miner.mine(panel, symbols, rounds=32, candidates_per_round=1, horizon=5)
    assert result["evaluated"] == 32
    assert len(result["accepted"]) == 30  # 前 30 个按基础门槛入库
    assert result["rejected"][-1]["reason"] == "未达门槛"  # 第 31 个起门槛收紧
