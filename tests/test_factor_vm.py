import numpy as np
import pytest

from engines.factor.vm import StackVM


def _features() -> dict[str, np.ndarray]:
    # 2 只标的 × 7 个交易日
    close = np.array([
        [10, 11, 12, 11, 13, 14, 15],
        [20, 19, 21, 22, 21, 23, 24],
    ], dtype=float)
    return {"close": close, "volume": np.ones_like(close)}


def test_ts_delay_div_hand_computed():
    vm = StackVM()
    features = _features()
    result = vm.execute(["close", "close", "ts_delay_5", "div"], features)
    assert result is not None
    # t=5: 14/10=1.4, 23/20=1.15 ; t=6: 15/11, 24/19
    assert result[0, 5] == pytest.approx(1.4)
    assert result[1, 5] == pytest.approx(1.15)
    assert result[0, 6] == pytest.approx(15 / 11)
    assert np.isnan(result[:, :5]).all()


def test_cs_rank_cross_section():
    vm = StackVM()
    result = vm.execute(["close", "cs_rank"], _features())
    assert result is not None
    # 每日截面：标的0 < 标的1，分位分别为 0.5 与 1.0
    assert (result[0, :] == pytest.approx(0.5))
    assert (result[1, :] == pytest.approx(1.0))


def test_invalid_token_returns_none():
    assert StackVM().execute(["close", "bogus_op"], _features()) is None


def test_unbalanced_formula_returns_none():
    # 栈中剩两个值
    assert StackVM().execute(["close", "close"], _features()) is None
    # 二元算子栈不足
    assert StackVM().execute(["close", "add", "add"], _features()) is None


def test_too_long_formula_returns_none():
    assert StackVM().execute(["close"] * 13, _features()) is None


def test_all_nan_result_returns_none():
    features = _features()
    # ts_delay_60 超过序列长度 → 全 NaN
    assert StackVM().execute(["close", "ts_delay_60"], features) is None
