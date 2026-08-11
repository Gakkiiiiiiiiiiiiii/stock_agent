from __future__ import annotations

from dataclasses import dataclass

from financial_agent.config import load_yaml_config

from engines.execution.models import ExecutionFill, ExecutionMode, ExecutionOrder, OrderStatus, TradeIntent
from engines.execution.qmt_adapter import BrokerAdapter
from engines.execution.risk_gate import validate_trade_intent


def load_execution_config() -> dict:
    try:
        return dict(load_yaml_config("execution.yaml").get("execution") or {})
    except FileNotFoundError:
        return {"mode": "PAPER", "max_single_order_weight": 0.10, "max_daily_turnover": 0.50}


@dataclass
class _StoredOrder:
    order: ExecutionOrder
    filled_quantity: int = 0


class ExecutionService:
    """Idempotent order manager.  Persistence can be added behind this boundary."""
    def __init__(self, mode: ExecutionMode | str | None = None, adapter: BrokerAdapter | None = None, rules: dict | None = None, repository=None) -> None:
        config = {**load_execution_config(), **(rules or {})}
        self.mode = ExecutionMode(mode or config.get("mode", "PAPER"))
        self.rules = config
        self.adapter = adapter
        self.repository = repository
        self._orders: dict[str, _StoredOrder] = {}

    def create_order(self, intent: TradeIntent, context: dict, quantity: int, limit_price: float | None = None) -> ExecutionOrder:
        existing = self._orders.get(intent.idempotency_key())
        if existing:
            return existing.order
        if self.repository is not None:
            existing_intent = self.repository.get_trade_intent_by_client_order_id(intent.idempotency_key())
            if existing_intent is not None:
                existing_order = self.repository.get_order_for_client_order_id(intent.idempotency_key())
                if existing_order is not None:
                    restored = ExecutionOrder(id=existing_order.id, client_order_id=intent.idempotency_key(), intent=TradeIntent.model_validate(existing_intent.payload), mode=existing_order.mode, status=existing_order.status, quantity=existing_order.quantity, limit_price=existing_order.limit_price, broker_order_id=existing_order.broker_order_id, rejection_reasons=list(existing_order.rejection_reasons or []))
                    self._orders[restored.client_order_id] = _StoredOrder(restored, sum(item.quantity for item in self.repository.list_fills(restored.id)))
                    return restored
        duplicate_context = {**context, "duplicate_order": False}
        reasons = validate_trade_intent(intent, duplicate_context, self.rules)
        order = ExecutionOrder(client_order_id=intent.idempotency_key(), intent=intent, mode=self.mode, quantity=quantity, limit_price=limit_price)
        if reasons:
            order.status = OrderStatus.REJECTED
            order.rejection_reasons = reasons
        else:
            order.status = OrderStatus.VALIDATED
        self._orders[order.client_order_id] = _StoredOrder(order)
        if self.repository is not None:
            intent_row = self.repository.create_trade_intent(
                client_order_id=intent.idempotency_key(), decision_id=intent.decision_id,
                strategy_id=intent.strategy_id, symbol=intent.symbol, target_version=intent.target_version,
                payload=intent.model_dump(mode="json"), status=order.status.value,
            )
            self.repository.add_order(
                id=order.id, trade_intent_id=intent_row.id, mode=order.mode.value, status=order.status.value,
                quantity=order.quantity, limit_price=order.limit_price, broker_order_id=order.broker_order_id,
                rejection_reasons=order.rejection_reasons,
            )
            self.repository.add_order_event(execution_order_id=order.id, status=order.status.value, payload={"reasons": order.rejection_reasons})
        return order

    def submit(self, client_order_id: str) -> ExecutionOrder:
        stored = self._orders[client_order_id]
        order = stored.order
        if order.status != OrderStatus.VALIDATED:
            return order
        if self.mode == ExecutionMode.SHADOW:
            return order
        if self.mode == ExecutionMode.LIVE:
            if self.adapter is None:
                order.status = OrderStatus.REJECTED
                order.rejection_reasons.append("LIVE_ADAPTER_UNAVAILABLE")
                return order
            order.broker_order_id = self.adapter.submit(order)
        order.status = OrderStatus.SUBMITTED
        if self.repository is not None:
            self.repository.update_order_status(order.id, order.status.value, broker_order_id=order.broker_order_id)
            self.repository.add_order_event(execution_order_id=order.id, status=order.status.value, payload={})
        return order

    def record_fill(self, fill: ExecutionFill) -> ExecutionOrder:
        stored = next((item for item in self._orders.values() if item.order.id == fill.order_id), None)
        if stored is None:
            raise KeyError(fill.order_id)
        stored.filled_quantity += fill.quantity
        if stored.filled_quantity > stored.order.quantity:
            raise ValueError("filled quantity exceeds order quantity")
        stored.order.status = OrderStatus.FILLED if stored.filled_quantity == stored.order.quantity else OrderStatus.PARTIALLY_FILLED
        if self.repository is not None:
            self.repository.update_order_status(stored.order.id, stored.order.status.value)
            self.repository.add_fill(
                execution_order_id=fill.order_id, quantity=fill.quantity, price=fill.price,
                broker_fill_id=fill.broker_fill_id, filled_at=fill.filled_at,
            )
            self.repository.add_order_event(execution_order_id=stored.order.id, status=stored.order.status.value, payload=fill.model_dump(mode="json"))
        return stored.order

    def order(self, client_order_id: str) -> ExecutionOrder | None:
        stored = self._orders.get(client_order_id)
        return stored.order if stored else None
