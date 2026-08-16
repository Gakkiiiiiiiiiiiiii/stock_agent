"""QuantExecutionClient（收尾文档 §31）。

Agent Execution 兼容路径内部转发到 quant trading.v1：
Paper Order / Plan 的权威在 Quant；Agent 不再作为本地 execution authority。
"""
from __future__ import annotations

from typing import Any

from clients.quant_client import RemoteQuantClient


class QuantExecutionClient:
    def __init__(self, client: RemoteQuantClient | None = None) -> None:
        self._client = client or RemoteQuantClient()
        self._account_id: str | None = None

    def ensure_account(self, name: str = "agent-execution", initial_cash: float = 1_000_000.0) -> str:
        if self._account_id:
            return self._account_id
        self._account_id = self._create_account(name, initial_cash)
        return self._account_id

    def _create_account(self, name: str, initial_cash: float) -> str:
        payload = self._client.request("POST", "/api/v1/paper/accounts", payload={"name": name, "initial_cash": initial_cash})
        data = payload.get("data", payload)
        return str(data["account_id"])

    def submit_paper_targets(self, targets: list[dict[str, Any]], signal_time: str | None = None) -> dict[str, Any]:
        """Agent TargetPortfolio -> quant paper plan + orders（trading.v1）。"""
        account_id = self.ensure_account()
        plan = self._client.request(
            "POST",
            "/api/v1/paper/plans",
            payload={"account_id": account_id, "targets": targets, "time_contract": {"signal_time": signal_time} if signal_time else None},
        )
        plan_data = plan.get("data", plan)
        generated = self._client.request(
            "POST",
            "/api/v1/paper/orders/generate",
            payload={"account_id": account_id, "as_of": signal_time, "plan_id": plan_data.get("plan_id")},
        )
        return {"authority": "quant/trading.v1", "account_id": account_id, "plan": plan_data, "orders": generated.get("data", generated)}

    def run_paper(self, as_of: str, market_prices: dict | None = None) -> dict[str, Any]:
        account_id = self.ensure_account()
        result = self._client.run_paper(account_id, as_of, market_prices)
        return {"authority": "quant/trading.v1", "account_id": account_id, **result}

    def paper_state(self) -> dict[str, Any]:
        account_id = self.ensure_account()
        state = self._client.get_paper_state(account_id)
        return {"authority": "quant/trading.v1", "account_id": account_id, **state}


__all__ = ["QuantExecutionClient"]
