from __future__ import annotations

from typing import Any

import pandas as pd

from engines.technical.models import RuleEvaluation, TriState


class RuleEngine:
    def __init__(self, max_depth: int = 12, max_nodes: int = 100) -> None:
        self.max_depth = max_depth
        self.max_nodes = max_nodes

    def evaluate_rule(self, rule: dict[str, Any], frame: pd.DataFrame, context: dict[str, Any] | None = None) -> RuleEvaluation:
        nodes: list[dict[str, Any]] = []
        status = self._eval(rule.get("condition") or {}, frame, context or {}, nodes, depth=0)
        max_score = float(rule.get("score") or 0.0)
        evidence = [str(item.get("template")) for item in rule.get("evidence") or [] if item.get("template")]
        return RuleEvaluation(
            rule_id=str(rule.get("id")),
            rule_version=str(rule.get("version") or "1.0.0"),
            status=status,
            score_awarded=max_score if status == TriState.TRUE else 0.0,
            max_score=max_score,
            evidence=evidence if status == TriState.TRUE else [],
            node_results=nodes,
        )

    def _eval(self, expr: Any, frame: pd.DataFrame, context: dict[str, Any], nodes: list[dict[str, Any]], depth: int) -> TriState:
        if depth > self.max_depth or len(nodes) >= self.max_nodes:
            return TriState.INDETERMINATE
        if not isinstance(expr, dict) or len(expr) != 1:
            return TriState.INDETERMINATE
        op, args = next(iter(expr.items()))
        if op == "all":
            results = [self._eval(item, frame, context, nodes, depth + 1) for item in args]
            status = TriState.FALSE if TriState.FALSE in results else (TriState.INDETERMINATE if TriState.INDETERMINATE in results else TriState.TRUE)
        elif op == "any":
            results = [self._eval(item, frame, context, nodes, depth + 1) for item in args]
            status = TriState.TRUE if TriState.TRUE in results else (TriState.INDETERMINATE if TriState.INDETERMINATE in results else TriState.FALSE)
        elif op in {"gt", "gte", "lt", "lte", "eq"}:
            left, right = self._value(args[0], frame, context), self._value(args[1], frame, context)
            status = self._compare(op, left, right)
        elif op == "cross_up":
            left = self._series(args[0], frame)
            right = self._series(args[1], frame)
            if left is None or right is None or len(left) < 2 or len(right) < 2:
                status = TriState.INDETERMINATE
            else:
                status = TriState.TRUE if left.iloc[-2] <= right.iloc[-2] and left.iloc[-1] > right.iloc[-1] else TriState.FALSE
        elif op == "context_gte":
            value = self._context_value(args[0], context)
            status = TriState.INDETERMINATE if value is None else (TriState.TRUE if float(value) >= float(args[1]) else TriState.FALSE)
        else:
            status = TriState.INDETERMINATE
        nodes.append({"op": op, "status": status.value})
        return status

    def _value(self, token: Any, frame: pd.DataFrame, context: dict[str, Any]) -> float | None:
        if isinstance(token, (int, float)):
            return float(token)
        if isinstance(token, str):
            series = self._series(token, frame)
            if series is not None and not series.empty:
                value = series.iloc[-1]
                return None if pd.isna(value) else float(value)
            value = self._context_value(token, context)
            return None if value is None else float(value)
        return None

    @staticmethod
    def _series(token: str, frame: pd.DataFrame) -> pd.Series | None:
        if token in frame:
            return frame[token]
        if "." in token:
            alias, column = token.split(".", 1)
            key = f"{alias}.{column}"
            if key in frame:
                return frame[key]
        return None

    @staticmethod
    def _context_value(path: str, context: dict[str, Any]) -> Any:
        cur: Any = context
        for part in str(path).split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    @staticmethod
    def _compare(op: str, left: float | None, right: float | None) -> TriState:
        if left is None or right is None:
            return TriState.INDETERMINATE
        if op == "gt":
            ok = left > right
        elif op == "gte":
            ok = left >= right
        elif op == "lt":
            ok = left < right
        elif op == "lte":
            ok = left <= right
        else:
            ok = left == right
        return TriState.TRUE if ok else TriState.FALSE
