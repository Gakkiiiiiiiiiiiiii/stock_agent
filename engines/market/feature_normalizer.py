"""横截面归一化纯函数：winsorize / percentile / robust zscore。

所有函数对 NaN/None 安全，且完全确定性：相同输入产生字节级一致输出。
"""
from __future__ import annotations

import math
from statistics import median
from typing import Sequence


def _is_missing(value: float | None) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _quantile(sorted_values: list[float], q: float) -> float:
    """线性插值分位数（与 numpy 默认 method='linear' 一致）。"""
    if not sorted_values:
        raise ValueError("empty series")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    lower_index = int(math.floor(position))
    upper_index = int(math.ceil(position))
    if lower_index == upper_index:
        return sorted_values[lower_index]
    fraction = position - lower_index
    return sorted_values[lower_index] * (1 - fraction) + sorted_values[upper_index] * fraction


def winsorize(values: Sequence[float | None], lower: float = 0.01, upper: float = 0.01) -> list[float | None]:
    """将序列裁剪到 [lower, 1-upper] 分位区间；缺失值（None/NaN）原样保留为 None。"""
    present = sorted(float(v) for v in values if not _is_missing(v))
    if not present:
        return [None for _ in values]
    lower_bound = _quantile(present, lower)
    upper_bound = _quantile(present, 1.0 - upper)
    result: list[float | None] = []
    for value in values:
        if _is_missing(value):
            result.append(None)
        else:
            result.append(min(upper_bound, max(lower_bound, float(value))))
    return result


def cross_sectional_percentile(values: Sequence[float | None]) -> list[float | None]:
    """将每个值映射为其在横截面中的百分位排名 [0, 100]。

    并列值取平均名次；缺失值返回 None。单值序列返回 [100.0]。
    """
    indexed = [(index, float(value)) for index, value in enumerate(values) if not _is_missing(value)]
    result: list[float | None] = [None for _ in values]
    if not indexed:
        return result
    if len(indexed) == 1:
        result[indexed[0][0]] = 100.0
        return result
    ordered = sorted(indexed, key=lambda item: (item[1], item[0]))
    denominator = len(ordered) - 1
    cursor = 0
    while cursor < len(ordered):
        end = cursor
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[cursor][1]:
            end += 1
        average_rank = (cursor + end) / 2.0  # 0 起始的平均名次
        percentile = average_rank / denominator * 100.0
        for position in range(cursor, end + 1):
            result[ordered[position][0]] = percentile
        cursor = end + 1
    return result


def robust_zscore(values: Sequence[float | None]) -> list[float | None]:
    """稳健 z 分数：(x - median) / (1.4826 * MAD)。MAD 为 0 时返回 0。"""
    present = [float(v) for v in values if not _is_missing(v)]
    if not present:
        return [None for _ in values]
    center = median(present)
    mad = median([abs(v - center) for v in present])
    scale = 1.4826 * mad
    result: list[float | None] = []
    for value in values:
        if _is_missing(value):
            result.append(None)
        elif scale == 0:
            result.append(0.0)
        else:
            result.append((float(value) - center) / scale)
    return result
