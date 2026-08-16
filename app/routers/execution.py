"""Execution API: accepts deterministic trade intents, never LLM broker calls.

收尾文档 §31：先保留兼容路径；AGENT_EXECUTION_AUTHORITY=quant 时，
PAPER 订单内部转发 QuantExecutionClient -> quant trading.v1，
Agent 不再作为本地 execution authority。
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from clients.quant_execution_client import QuantExecutionClient
from engines.execution.models import ExecutionFill, ExecutionMode, TradeIntent
from engines.execution.service import ExecutionService
from storage.repositories.p2_repository import P2Repository

router = APIRouter(prefix="/api/v1/execution", tags=["execution"])
_services: dict[ExecutionMode, ExecutionService] = {}
_quant_execution = QuantExecutionClient()


def _execution_authority() -> str:
    return os.getenv("AGENT_EXECUTION_AUTHORITY", "local").lower()


def _service(mode: ExecutionMode) -> ExecutionService:
    return _services.setdefault(mode, ExecutionService(mode=mode, repository=P2Repository()))


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
    if _execution_authority() == "quant":
        # §31：Paper/执行权威在 quant trading.v1；Agent 只做兼容 proxy。
        if request.mode == ExecutionMode.LIVE:
            raise HTTPException(status_code=501, detail="EXECUTION_REJECTED: live authority not wired to quant")
        try:
            return _quant_execution.submit_paper_targets(
                [{"symbol": request.intent.symbol, "target_weight": request.intent.target_weight}],
                signal_time=datetime.now(UTC).isoformat(timespec="seconds"),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=f"EXECUTION_REJECTED: quant unavailable: {exc}") from exc
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


@router.post("/orders/{client_order_id}/cancel")
def cancel_order(client_order_id: str, mode: ExecutionMode = ExecutionMode.PAPER) -> dict:
    try:
        return _service(mode).cancel(client_order_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ORDER_NOT_FOUND") from exc


@router.post("/paper/quotes/{symbol}")
def paper_quote(symbol: str, quote: dict) -> dict:
    return {"orders": [order.model_dump(mode="json") for order in _service(ExecutionMode.PAPER).process_quote(symbol, quote)]}


@router.post("/halt")
def set_halt(halted: bool, mode: ExecutionMode = ExecutionMode.PAPER, reason: str | None = None) -> dict:
    _service(mode).set_halted(halted, reason)
    return _service(mode).status()


@router.post("/reconcile")
def reconcile_positions(local: dict, broker: dict) -> dict:
    return _service(ExecutionMode.PAPER).reconcile(local, broker)
