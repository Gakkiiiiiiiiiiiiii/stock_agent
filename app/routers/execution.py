"""Execution API: accepts deterministic trade intents, never LLM broker calls."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engines.execution.models import ExecutionFill, ExecutionMode, TradeIntent
from engines.execution.reconciliation import reconcile
from engines.execution.service import ExecutionService

router = APIRouter(prefix="/api/v1/execution", tags=["execution"])
_services: dict[ExecutionMode, ExecutionService] = {}


def _service(mode: ExecutionMode) -> ExecutionService:
    return _services.setdefault(mode, ExecutionService(mode=mode))


class CreateOrderRequest(BaseModel):
    intent: TradeIntent
    context: dict = Field(default_factory=dict)
    quantity: int = Field(gt=0)
    limit_price: float | None = Field(default=None, gt=0)
    mode: ExecutionMode = ExecutionMode.PAPER


class FillRequest(BaseModel):
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)
    broker_fill_id: str | None = None


@router.post("/orders")
def create_order(request: CreateOrderRequest) -> dict:
    order = _service(request.mode).create_order(request.intent, request.context, request.quantity, request.limit_price)
    return order.model_dump(mode="json")


@router.post("/orders/{client_order_id}/submit")
def submit_order(client_order_id: str, mode: ExecutionMode = ExecutionMode.PAPER) -> dict:
    service = _service(mode)
    if service.order(client_order_id) is None:
        raise HTTPException(status_code=404, detail="ORDER_NOT_FOUND")
    return service.submit(client_order_id).model_dump(mode="json")


@router.post("/orders/{client_order_id}/fills")
def record_fill(client_order_id: str, request: FillRequest, mode: ExecutionMode = ExecutionMode.PAPER) -> dict:
    service = _service(mode)
    order = service.order(client_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="ORDER_NOT_FOUND")
    fill = ExecutionFill(order_id=order.id, quantity=request.quantity, price=request.price, broker_fill_id=request.broker_fill_id)
    return service.record_fill(fill).model_dump(mode="json")


@router.post("/reconcile")
def reconcile_positions(local: dict, broker: dict) -> dict:
    return reconcile(local, broker)
