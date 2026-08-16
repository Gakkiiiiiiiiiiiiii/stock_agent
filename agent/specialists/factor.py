from __future__ import annotations

from agent.contracts import AgentRole, AgentTask
from agent.specialists.base import ToolSpecialist
from contracts.factor import AlphaScoreRequest


class FactorSpecialist(ToolSpecialist):
    """调用 stock_factor 的 On-demand Alpha Score（设计文档 §14.2 / §79）。

    降级语义（§62 / §90）：Factor 服务不可用时不得伪造 factor evidence，
    必须显式报告 FACTOR_UNAVAILABLE 并把决策质量标记为 DEGRADED。
    """

    role = AgentRole.FACTOR

    def __init__(self, registry, context: dict | None = None, factor_client=None) -> None:
        super().__init__(registry, context)
        self._client = factor_client

    def __call__(self, task: AgentTask, _shared):
        symbols = self.context.get("universe") or self.context.get("symbols") or []
        if not symbols:
            return self.artifact(task, {"factor_evidence": [], "decision_quality": "DEGRADED"}, ["FACTOR_UNIVERSE_NOT_PROVIDED"], 0)
        client = self._client
        if client is None:
            from services.subsystems import get_factor_client

            client = get_factor_client()
        try:
            response = client.score_alpha(
                AlphaScoreRequest(symbols=list(symbols), as_of=task.as_of.date().isoformat())
            )
        except Exception:  # noqa: BLE001
            return self.artifact(
                task,
                {"factor_evidence": [], "decision_quality": "DEGRADED"},
                ["FACTOR_UNAVAILABLE"],
                0,
            )
        scores = response.get("scores") or []
        factor_evidence = [
            {
                "type": "factor_score",
                "source_id": "stock_factor:/api/v1/alpha/score",
                "snapshot_id": response.get("market_snapshot_id") or response.get("data_snapshot_id"),
                "symbol": item.get("symbol"),
                "score": item.get("score"),
                "rank": item.get("rank"),
                "evidence": item.get("evidence") or [],
            }
            for item in scores
        ]
        conclusion = {
            "factor_evidence": factor_evidence,
            "factor_set_version": response.get("factor_set_version"),
            "market_snapshot_id": response.get("market_snapshot_id") or response.get("data_snapshot_id"),
            "factor_scores": scores,
            "as_of": response.get("as_of"),
        }
        return self.artifact(task, conclusion, [], 1)
