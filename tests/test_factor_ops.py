"""因子 DSL 新增算子手算用例 + Alpha191 种子库可执行性验证。"""
from pathlib import Path

import numpy as np
import pytest
import yaml

from engines.factor.ops import get_op
from engines.factor.vocab import MAX_FORMULA_TOKENS, TS_WINDOWS, is_valid_token
from engines.factor.vm import StackVM

SEED_PATH = Path(__file__).resolve().parent.parent / "config" / "factor_seed_alpha191.yaml"


def _op1(token: str, x: np.ndarray) -> np.ndarray:
    func, arity = get_op(token)
    assert arity == 1
    return func(x)


def _op2(token: str, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    func, arity = get_op(token)
    assert arity == 2
    return func(a, b)


# ---------------------------------------------------------------- 词表


def test_new_windows_registered():
    for w in (4, 8, 15, 30, 120):
        assert w in TS_WINDOWS
        assert is_valid_token(f"ts_mean_{w}")


def test_new_token_names_valid():
    for token in (
        "ts_corr_10", "ts_cov_10", "ts_sum_5", "decay_linear_10",
        "ts_argmax_20", "ts_argmin_20", "count_10", "signedpower", "where",
    ):
        assert is_valid_token(token), token
    # 无窗口后缀 / 窗口不在词表 / 未知算子仍非法
    assert not is_valid_token("ts_corr")
    assert not is_valid_token("ts_mean_7")
    assert not is_valid_token("ts_foo_10")
    assert not is_valid_token("signedpower_10")


def test_max_formula_tokens_is_16():
    assert MAX_FORMULA_TOKENS == 16


def test_get_op_arity():
    assert get_op("ts_corr_10")[1] == 2
    assert get_op("ts_cov_5")[1] == 2
    assert get_op("where")[1] == 3
    assert get_op("signedpower")[1] == 1
    for token in ("ts_sum_10", "decay_linear_10", "ts_argmax_10", "ts_argmin_10", "count_10"):
        assert get_op(token)[1] == 1, token


# ---------------------------------------------------------------- ts_sum / decay_linear


def test_ts_sum_hand_computed():
    x = np.array([[1.0, 2.0, 3.0, 4.0]])
    out = _op1("ts_sum_3", x)
    assert np.isnan(out[0, :2]).all()
    assert out[0, 2] == pytest.approx(6.0)
    assert out[0, 3] == pytest.approx(9.0)


def test_ts_sum_nan_boundary():
    x = np.array([[np.nan, np.nan, 1.0, np.nan]])
    out = _op1("ts_sum_3", x)
    # 窗 [nan,nan,1] → 1；窗 [nan,1,nan] → 1（NaN 视为缺失跳过）
    assert np.isnan(out[0, :2]).all()
    assert out[0, 2] == pytest.approx(1.0)
    assert out[0, 3] == pytest.approx(1.0)
    # 全 NaN 窗输出 NaN（nansum 默认得 0，需掩蔽）
    assert np.isnan(_op1("ts_sum_3", np.array([[np.nan, np.nan, np.nan]]))[0, 2])


def test_decay_linear_hand_computed():
    x = np.array([[1.0, 2.0, 3.0]])
    out = _op1("decay_linear_3", x)
    # 权重 (1/6, 2/6, 3/6)，最新值权重最大：(1*1 + 2*2 + 3*3)/6 = 14/6
    assert out[0, 2] == pytest.approx(14.0 / 6.0)
    assert np.isnan(out[0, :2]).all()


def test_decay_linear_nan_renormalized():
    x = np.array([[np.nan, 2.0, 4.0]])
    out = _op1("decay_linear_3", x)
    # 首值缺失 → 有效权重 (2/6, 3/6) 归一化：(2*2 + 4*3)/5 = 16/5
    assert out[0, 2] == pytest.approx(16.0 / 5.0)
    # 全 NaN 窗 → NaN
    assert np.isnan(_op1("decay_linear_3", np.array([[np.nan, np.nan, np.nan]]))[0, 2])


# ---------------------------------------------------------------- ts_argmax / ts_argmin


def test_ts_argmax_argmin_hand_computed():
    x = np.array([[1.0, 3.0, 2.0, 5.0, 4.0]])
    argmax = _op1("ts_argmax_3", x)
    # t=2: 窗 [1,3,2] 最大在 idx1 → 距今 1；t=3: [3,2,5] → 0；t=4: [2,5,4] → 1
    assert argmax[0, 2:] == pytest.approx([1.0, 0.0, 1.0])
    assert np.isnan(argmax[0, :2]).all()

    argmin = _op1("ts_argmin_3", x)
    # t=2: 最小在 idx0 → 距今 2；t=3: idx1 → 1；t=4: idx0 → 2
    assert argmin[0, 2:] == pytest.approx([2.0, 1.0, 2.0])


def test_ts_argmax_all_nan_window():
    x = np.array([[np.nan, np.nan, np.nan, 1.0]])
    out = _op1("ts_argmax_3", x)
    assert np.isnan(out[0, 2])  # 全 NaN 窗
    assert out[0, 3] == pytest.approx(0.0)  # 窗 [nan,nan,1] 极值在当期


# ---------------------------------------------------------------- count


def test_count_hand_computed():
    x = np.array([[1.0, -1.0, 2.0, 0.0, 3.0]])
    out = _op1("count_3", x)
    # t=2: [1,-1,2] → 2；t=3: [-1,2,0] → 1（0 不计）；t=4: [2,0,3] → 2
    assert out[0, 2:] == pytest.approx([2.0, 1.0, 2.0])
    assert np.isnan(out[0, :2]).all()


def test_count_nan_boundary():
    x = np.array([[np.nan, 1.0, np.nan, np.nan]])
    out = _op1("count_3", x)
    assert out[0, 2] == pytest.approx(1.0)  # 窗 [nan,1,nan]，NaN 不计入
    assert out[0, 3] == pytest.approx(1.0)  # 窗 [1,nan,nan]
    # 全 NaN 窗 → NaN
    assert np.isnan(_op1("count_3", np.array([[np.nan, np.nan, np.nan]]))[0, 2])


# ---------------------------------------------------------------- ts_corr / ts_cov


def test_ts_corr_matches_numpy_corrcoef():
    rng = np.random.default_rng(7)
    a = rng.normal(size=(4, 30))
    b = rng.normal(size=(4, 30))
    out = _op2("ts_corr_5", a, b)
    assert np.isnan(out[:, :4]).all()
    for s in range(4):
        for t in range(4, 30):
            expected = np.corrcoef(a[s, t - 4:t + 1], b[s, t - 4:t + 1])[0, 1]
            assert out[s, t] == pytest.approx(expected, abs=1e-10)


def test_ts_corr_nan_and_constant_window():
    a = np.array([[1.0, 2.0, 3.0]])
    b_const = np.array([[5.0, 5.0, 5.0]])
    # 常数序列零方差 → NaN
    assert np.isnan(_op2("ts_corr_3", a, b_const)[0, 2])
    # 有效配对 <2 → NaN
    a_nan = np.array([[np.nan, np.nan, 1.0]])
    b_nan = np.array([[1.0, 2.0, 3.0]])
    assert np.isnan(_op2("ts_corr_3", a_nan, b_nan)[0, 2])


def test_ts_cov_hand_computed():
    a = np.array([[1.0, 2.0, 3.0]])
    b = np.array([[2.0, 4.0, 6.0]])
    out = _op2("ts_cov_3", a, b)
    # 总体协方差（ddof=0）：mean(ab)=28/3，mean(a)*mean(b)=2*4=8 → 28/3-8=4/3
    assert out[0, 2] == pytest.approx(4.0 / 3.0)
    assert np.isnan(out[0, :2]).all()


# ---------------------------------------------------------------- signedpower / where


def test_signedpower_hand_computed():
    x = np.array([[-2.0, 0.0, 3.0]])
    out = _op1("signedpower", x)
    assert out == pytest.approx(np.array([[-4.0, 0.0, 9.0]]))


def test_where_hand_computed():
    func, arity = get_op("where")
    assert arity == 3
    cond = np.array([[1.0, -1.0, 0.0]])
    a = np.full((1, 3), 10.0)
    b = np.full((1, 3), 20.0)
    # cond>0 取 a，否则（含 0 与负值）取 b
    assert func(cond, a, b) == pytest.approx(np.array([[10.0, 20.0, 20.0]]))


# ---------------------------------------------------------------- VM 集成


def _vm_features(n_symbols: int = 20, n_days: int = 260, seed: int = 42) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    close = 10.0 * np.exp(np.cumsum(rng.normal(0, 0.02, (n_symbols, n_days)), axis=1))
    high = close * (1 + rng.uniform(0, 0.02, close.shape))
    low = close * (1 - rng.uniform(0, 0.02, close.shape))
    open_ = low + (high - low) * rng.uniform(0, 1, close.shape)
    volume = rng.uniform(1e6, 1e7, close.shape)
    vwap = (high + low + close) / 3.0
    amount = volume * vwap
    turnover = rng.uniform(0.5, 5.0, close.shape)
    ret = np.full_like(close, np.nan)
    ret[:, 1:] = close[:, 1:] / close[:, :-1] - 1.0
    return {
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
        "amount": amount, "turnover": turnover, "vwap": vwap, "ret": ret,
    }


def test_vm_executes_new_operators():
    vm = StackVM()
    features = _vm_features()
    # where 三元算子：价格在 20 日均线上方取 ret，否则取 -ret
    rpn = ["close", "close", "ts_mean_20", "gt", "ret", "ret", "neg", "where", "cs_rank"]
    result = vm.execute(rpn, features)
    assert result is not None
    assert not np.isnan(result).all()
    # 二元时序算子经 VM 执行
    assert vm.execute(["close", "volume", "ts_corr_10"], features) is not None


def test_vm_max_tokens_16():
    vm = StackVM()
    features = _vm_features()
    rpn16 = ["close", "close", "ts_delay_5", "div", "neg",
             "close", "close", "ts_mean_20", "div", "neg",
             "add", "signedpower", "abs", "log", "abs", "cs_rank"]
    assert len(rpn16) == 16
    assert vm.execute(rpn16, features) is not None
    # 17 个 token 超限
    assert vm.execute(["close"] * 17, features) is None


# ---------------------------------------------------------------- 种子库


def test_seed_alpha191_executable():
    data = yaml.safe_load(SEED_PATH.read_text(encoding="utf-8"))
    seeds = data["seeds"]
    assert 8 <= len(seeds) <= 12
    vm = StackVM()
    features = _vm_features()
    for seed in seeds:
        assert set(seed) == {"name", "hypothesis", "rpn"}, seed
        rpn = seed["rpn"]
        assert len(rpn) <= MAX_FORMULA_TOKENS, seed["name"]
        assert all(is_valid_token(t) for t in rpn), seed["name"]
        result = vm.execute(rpn, features)
        assert result is not None, seed["name"]
        assert not np.isnan(result).all(), seed["name"]
