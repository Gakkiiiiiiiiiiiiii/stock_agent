from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class ExecutionMode(StrEnum):
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    LIVE = "LIVE"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class TradeIntent(BaseModel):
    decision_id: str
    symbol: str
    target_weight: float = Field(ge=0, le=1)
    current_weight: float = Field(ge=0, le=1)
    delta_weight: float | None = None
    side: str | None = None
    urgency: str = "NORMAL"
    execution_model: str = "NEXT_OPEN"
    max_slippage_bps: float = Field(default=30, ge=0)
    reason_codes: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    target_version: str = "v1"
    strategy_id: str | None = None
    client_order_id: str | None = None

    @model_validator(mode="after")
    def derive_fields(self) -> "TradeIntent":
        delta = round(self.target_weight - self.current_weight, 8)
        if self.delta_weight is not None and abs(self.delta_weight - delta) > 1e-8:
            raise ValueError("delta_weight does not match target_weight-current_weight")
        self.delta_weight = delta
        expected_side = "BUY" if delta > 0 else "SELL" if delta < 0 else "HOLD"
        if self.side is not None and self.side != expected_side:
            raise ValueError("side does not match delta_weight")
        self.side = expected_side
        self.client_order_id = self.client_order_id or self.idempotency_key()
        return self

    def idempotency_key(self) -> str:
        return f"{self.decision_id}:{self.symbol}:{self.target_version}"


class ExecutionOrder(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    client_order_id: str
    intent: TradeIntent
    mode: ExecutionMode
    status: OrderStatus = OrderStatus.CREATED
    quantity: int = Field(ge=0)
    limit_price: float | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    rejection_reasons: list[str] = Field(default_factory=list)
    broker_order_id: str | None = None


class ExecutionFill(BaseModel):
    order_id: str
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)
    filled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    broker_fill_id: str | None = None

