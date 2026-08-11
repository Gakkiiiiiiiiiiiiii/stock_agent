"""Broker adapter protocol; a live adapter cannot contain strategy decisions."""
from __future__ import annotations

from typing import Protocol

from engines.execution.models import ExecutionOrder


class BrokerAdapter(Protocol):
    def submit(self, order: ExecutionOrder) -> str: ...
    def cancel(self, broker_order_id: str) -> None: ...
    def snapshot(self) -> dict: ...


class DryRunQmtAdapter:
    """Contract-test adapter which never opens a broker connection."""
    def submit(self, order: ExecutionOrder) -> str:
        return f"DRYRUN-{order.client_order_id}"

    def cancel(self, broker_order_id: str) -> None:
        return None

    def snapshot(self) -> dict:
        return {"cash": 0.0, "positions": {}}
