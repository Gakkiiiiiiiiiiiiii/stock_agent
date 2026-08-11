"""feature_normalizer 纯函数测试：正确性、NaN 处理、确定性。"""
from __future__ import annotations

import math

from engines.market.feature_normalizer import (
    cross_sectional_percentile,
    robust_zscore,
    winsorize,
)


def test_winsorize_clips_extremes_to_quantiles():
    values = [float(i) for i in range(1, 102)]  # 1..101
    result = winsorize(values, lower=0.05, upper=0.05)
    # 5% 分位 = 1 + 0.05*100 = 6.0；95% 分位 = 96.0
    assert result[0] == 6.0
    assert result[-1] == 96.0
    assert result[50] == 51.0  # 中位数不变


def test_winsorize_zero_bounds_keep_series_unchanged():
    assert winsorize([3.0, 1.0, 2.0], lower=0.0, upper=0.0) == [3.0, 1.0, 2.0]


def test_winsorize_default_bounds_clip_short_series_to_interpolated_quantiles():
    # [1,2,3] 的 1% 分位 = 1.02，99% 分位 = 2.98
    assert winsorize([3.0, 1.0, 2.0]) == [2.98, 1.02, 2.0]


def test_winsorize_handles_empty_and_missing():
    assert winsorize([]) == []
    assert winsorize([None, float("nan")]) == [None, None]
    result = winsorize([1.0, None, 100.0, float("nan")], lower=0.1, upper=0.1)
    assert result[1] is None
    assert result[3] is None
    assert result[0] is not None and result[2] is not None


def test_cross_sectional_percentile_basic():
    assert cross_sectional_percentile([10.0, 20.0, 30.0]) == [0.0, 50.0, 100.0]


def test_cross_sectional_percentile_ties_use_average_rank():
    # 两个 1.0 并列名次 1/2 → 平均 1.5 → (1.5-1)/(3-1)*100 = 25
    assert cross_sectional_percentile([1.0, 1.0, 2.0]) == [25.0, 25.0, 100.0]


def test_cross_sectional_percentile_nan_maps_to_none():
    result = cross_sectional_percentile([1.0, float("nan"), 3.0, None])
    assert result[0] == 0.0
    assert result[1] is None
    assert result[2] == 100.0
    assert result[3] is None


def test_cross_sectional_percentile_single_and_empty():
    assert cross_sectional_percentile([]) == []
    assert cross_sectional_percentile([5.0]) == [100.0]


def test_robust_zscore_known_values():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = robust_zscore(values)
    center = 3.0
    mad = 1.0  # median(|x-3|) = median([2,1,0,1,2])
    scale = 1.4826 * mad
    assert result[2] == 0.0
    assert math.isclose(result[4], (5.0 - center) / scale)
    assert math.isclose(result[0], (1.0 - center) / scale)


def test_robust_zscore_zero_mad_returns_zero():
    assert robust_zscore([7.0, 7.0, 7.0]) == [0.0, 0.0, 0.0]


def test_robust_zscore_nan_safe():
    result = robust_zscore([1.0, float("nan"), 3.0])
    assert result[1] is None
    assert result[0] is not None
    assert robust_zscore([]) == []


def test_all_functions_deterministic():
    values = [3.5, float("nan"), -1.2, 3.5, 0.0, 42.0, None]
    for fn in (winsorize, cross_sectional_percentile, robust_zscore):
        first = fn(values)
        second = fn(list(values))
        assert repr(first) == repr(second)
