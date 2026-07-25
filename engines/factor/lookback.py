from __future__ import annotations

from engines.factor.ops import parse_ts_token
from engines.factor.vocab import BINARY_OPS, CS_OPS, FEATURES, TERNARY_OPS, TS_BINARY_OPS, UNARY_OPS


FORBIDDEN_FUTURE_TOKENS = (
    "lead",
    "future_return",
    "negative_delay",
    "centered_rolling",
)


def max_lookback_from_rpn(rpn: list[str]) -> int:
    """Estimate the largest historical window needed by an RPN formula."""
    stack: list[int] = []
    for raw in rpn:
        token = str(raw)
        if any(marker in token for marker in FORBIDDEN_FUTURE_TOKENS):
            raise ValueError(f"future-looking token is forbidden: {token}")
        parsed = parse_ts_token(token)
        if token in FEATURES:
            stack.append(1)
        elif parsed:
            name, window = parsed
            if name in TS_BINARY_OPS:
                right = _pop(stack, token)
                left = _pop(stack, token)
                stack.append(max(left, right) + window - 1)
            elif name in {"ts_delay", "ts_delta"}:
                value = _pop(stack, token)
                stack.append(value + window)
            else:
                value = _pop(stack, token)
                stack.append(value + window - 1)
        elif token in CS_OPS or token in UNARY_OPS:
            stack.append(_pop(stack, token))
        elif token in BINARY_OPS:
            right = _pop(stack, token)
            left = _pop(stack, token)
            stack.append(max(left, right))
        elif token in TERNARY_OPS:
            c = _pop(stack, token)
            a = _pop(stack, token)
            b = _pop(stack, token)
            stack.append(max(c, a, b))
        else:
            raise ValueError(f"unknown factor token: {token}")
    if len(stack) != 1:
        raise ValueError("invalid RPN expression")
    return max(1, int(stack[0]))


def _pop(stack: list[int], token: str) -> int:
    if not stack:
        raise ValueError(f"not enough operands for token: {token}")
    return stack.pop()


__all__ = ["max_lookback_from_rpn", "FORBIDDEN_FUTURE_TOKENS"]
