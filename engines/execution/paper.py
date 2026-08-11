from __future__ import annotations

from engines.execution.models import ExecutionFill, ExecutionOrder, OrderStatus


class PaperFillSimulator:
    """Deterministic next-quote paper fill; no manual test-only fill injection."""
    def fill(self, order: ExecutionOrder, quote: dict) -> ExecutionFill | None:
        if order.status != OrderStatus.SUBMITTED or order.quantity <= 0:
            return None
        price = float(quote.get("open") or quote.get("last") or 0)
        if price <= 0 or quote.get("suspended"):
            return None
        return ExecutionFill(order_id=order.id, quantity=order.quantity, price=price, broker_fill_id=f"PAPER-{order.client_order_id}")
