"""Read-only execution status facade.

Decision Authority may display execution state owned by quant, but it cannot
create accounts, plans, orders, fills, or submit/cancel anything.
"""
from __future__ import annotations

from clients.quant_client import RemoteQuantClient


class QuantExecutionClient:
    def __init__(self, client: RemoteQuantClient | None = None) -> None:
        self._client = client or RemoteQuantClient()
    def get_execution_status(self, account_id: str | None = None) -> dict:
        """Return status only; quant remains the execution authority."""
        return self._client.get_execution_status(account_id)


__all__ = ["QuantExecutionClient"]
