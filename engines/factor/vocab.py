from __future__ import annotations

# 特征词表：对齐 financial_agent.models.KlineRecord 的可用字段。
# vwap = amount / volume；ret = 日收益率；turnover 缺失时退化为 0。
FEATURES: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "turnover",
    "vwap",
    "ret",
)

# 时序算子的可选窗口（烘焙进 token 名，如 ts_mean_10）。
TS_WINDOWS: tuple[int, ...] = (3, 5, 10, 20, 60)

# 时序算子（一元，窗口后缀）：对单个标的的时间序列做滚动计算。
TS_OPS: tuple[str, ...] = ("ts_mean", "ts_std", "ts_max", "ts_min", "ts_delta", "ts_delay", "ts_rank")

# 横截面算子（一元）：按日期在标的维度上计算，是横截面选股因子的关键。
CS_OPS: tuple[str, ...] = ("cs_rank", "cs_zscore", "cs_demean")

# 逐元素一元算子。
UNARY_OPS: tuple[str, ...] = ("neg", "abs", "log", "sqrt", "sign")

# 逐元素二元算子。
BINARY_OPS: tuple[str, ...] = ("add", "sub", "mul", "div", "gt", "lt", "max", "min")

# 合法 op token 全集。
ALL_OP_TOKENS: frozenset[str] = frozenset(
    [f"{name}_{window}" for name in TS_OPS for window in TS_WINDOWS]
    + list(CS_OPS)
    + list(UNARY_OPS)
    + list(BINARY_OPS)
)

FEATURE_SET: frozenset[str] = frozenset(FEATURES)

# 公式长度上限（防过拟合与组合爆炸）。
MAX_FORMULA_TOKENS = 12


def is_valid_token(token: str) -> bool:
    return token in FEATURE_SET or token in ALL_OP_TOKENS
