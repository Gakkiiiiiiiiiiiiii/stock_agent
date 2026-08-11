"""Deterministic source for StrategyFactory promotion metrics."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import numpy as np

from engines.strategy_factory.compiler import compile_strategy
from engines.strategy_factory.evaluator import evaluate_strategy
from engines.factor.purged_walkforward import run_purged_walkforward


class StrategyEvaluationRunner:
    def __init__(self, repository=None) -> None:
        self.repository = repository
        self._runs: dict[str, dict] = {}

    def run(self, definition, dataset: dict, evaluation_type: str = "BACKTEST") -> dict:
        compiled = compile_strategy(definition)
        result = evaluate_strategy(compiled, **dataset)
        curve = np.asarray(result["backtest"]["equity_curve"], dtype=float)
        peak = np.maximum.accumulate(curve)
        drawdown = float(np.max(1 - curve / np.maximum(peak, 1e-9))) if len(curve) else 1.0
        metrics = {**result["statistics"], "max_drawdown": drawdown, "turnover": float(np.mean(result["backtest"]["daily_turnover"])) if result["backtest"]["daily_turnover"] else 0.0, "oos_days": len(dataset["dates"]), "excess_sharpe": result["statistics"].get("deflated_sharpe", 0.0)}
        if evaluation_type == "OOS":
            walkforward = run_purged_walkforward(np.asarray(dataset["scores"]), np.asarray(dataset["closes"]))
            metrics["purged_walkforward"] = walkforward
            metrics["oos_days"] = sum(len(item.get("test") or []) for item in walkforward.get("windows") or [])
            metrics["excess_sharpe"] = float(result["statistics"].get("deflated_sharpe", 0.0))
        passed = bool(result["passed"] and not result.get("quality_flags"))
        if evaluation_type == "OOS":
            passed = passed and bool(metrics["purged_walkforward"].get("passed"))
        run = {"id": str(uuid4()), "strategy_id": definition.strategy_id, "evaluation_type": evaluation_type, "data_as_of": datetime.now(UTC), "data_snapshot_id": _snapshot_id(dataset), "metrics": metrics, "quality_flags": list(result.get("quality_flags") or []), "passed": passed}
        self._runs[run["id"]] = run
        if self.repository is not None:
            row = self.repository.create_strategy_evaluation(**run)
            run["id"] = row.id
        return run

    def get(self, evaluation_run_id: str) -> dict | None:
        if evaluation_run_id in self._runs:
            return self._runs[evaluation_run_id]
        if self.repository is None:
            return None
        row = self.repository.get_strategy_evaluation(evaluation_run_id)
        return None if row is None else {"id": row.id, "strategy_id": row.strategy_id, "evaluation_type": row.evaluation_type, "metrics": row.metrics, "quality_flags": row.quality_flags, "passed": row.passed}


def _snapshot_id(dataset: dict) -> str | None:
    metadata = dataset.get("score_metadata") or []
    return str((metadata[0] or {}).get("data_snapshot_id")) if metadata else None
