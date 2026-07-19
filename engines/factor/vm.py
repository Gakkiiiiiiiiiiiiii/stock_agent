"""RPN 因子公式栈式虚拟机。

输入 RPN token 序列与特征面板（dict[特征名, (n_symbols, n_days) ndarray]），
输出同形状的因子值面板；公式非法或计算失败时返回 None。
"""
from __future__ import annotations

import logging

import numpy as np

from engines.factor.ops import get_op
from engines.factor.vocab import FEATURES, MAX_FORMULA_TOKENS

logger = logging.getLogger(__name__)


class StackVM:
    """极简栈式虚拟机：特征名压栈，算子按元数弹栈求值，结束时栈中须恰好剩一个值。"""

    def __init__(self, max_tokens: int = MAX_FORMULA_TOKENS):
        self.max_tokens = max_tokens

    def execute(self, rpn: list[str], features: dict[str, np.ndarray]) -> np.ndarray | None:
        if not rpn or len(rpn) > self.max_tokens:
            return None

        stack: list[np.ndarray] = []
        try:
            with np.errstate(all="ignore"):
                for token in rpn:
                    if token in FEATURES:
                        panel = features.get(token)
                        if panel is None:
                            return None
                        stack.append(np.asarray(panel, dtype=float))
                        continue

                    op = get_op(token)
                    if op is None:
                        return None
                    func, arity = op
                    if len(stack) < arity:
                        return None
                    args = stack[-arity:] if arity > 1 else [stack[-1]]
                    del stack[-arity:]
                    result = func(*args)
                    result = np.nan_to_num(np.asarray(result, dtype=float), nan=np.nan,
                                           posinf=np.nan, neginf=np.nan)
                    stack.append(result)
        except Exception as exc:  # noqa: BLE001 - 非法公式视为计算失败
            logger.debug("因子公式执行失败 %s: %s", rpn, exc)
            return None

        if len(stack) != 1:
            return None
        result = stack[0]
        if np.isnan(result).all():
            return None
        return result


__all__ = ["StackVM"]
