"""Hard pre-trade checks.  No LLM is involved in this module."""
from __future__ import annotations

from datetime import UTC, datetime

from engines.execution.models import TradeIntent


def validate_trade_intent(intent: TradeIntent, context: dict, rules: dict) -> list[str]:
    reasons: list[str] = []
    quote = dict(context.get("quote") or {})
    now = context.get("now") or datetime.now(UTC)
    max_age = float(rules.get("stale_market_data_minutes", 5)) * 60
    as_of = quote.get("as_of")
    if isinstance(as_of, str):
        as_of = datetime.fromisoformat(as_of)
    if not isinstance(as_of, datetime) or (now - as_of).total_seconds() > max_age:
        reasons.append("STALE_MARKET_DATA")
    if quote.get("suspended"):
        reasons.append("SUSPENDED")
    if intent.side == "BUY" and quote.get("at_limit_up"):
        reasons.append("PRICE_LIMIT_UP")
    if intent.side == "SELL" and quote.get("at_limit_down"):
        reasons.append("PRICE_LIMIT_DOWN")
    if abs(intent.delta_weight or 0) > float(rules.get("max_single_order_weight", 0.10)):
        reasons.append("SINGLE_ORDER_LIMIT")
    if intent.target_weight > float(context.get("max_single_stock", rules.get("max_single_stock", 1))):
        reasons.append("POSITION_LIMIT")
    if float(context.get("projected_total_weight", 0)) > float(context.get("max_total_weight", 1)):
        reasons.append("PORTFOLIO_GROSS_LIMIT")
    if float(context.get("projected_theme_weight", 0)) > float(context.get("max_theme_weight", 1)):
        reasons.append("THEME_LIMIT")
    if float(context.get("projected_sector_weight", 0)) > float(context.get("max_sector_weight", 1)):
        reasons.append("SECTOR_LIMIT")
    if float(context.get("daily_turnover", 0)) + abs(intent.delta_weight or 0) > float(rules.get("max_daily_turnover", 1)):
        reasons.append("DAILY_TURNOVER_LIMIT")
    if not quote.get("liquid", True):
        reasons.append("LIQUIDITY_LIMIT")
    if intent.side == "BUY" and float(context.get("available_cash", 0)) < float(context.get("order_notional", 0)):
        reasons.append("INSUFFICIENT_CASH")
    if intent.side == "SELL" and float(context.get("available_position", 0)) < float(context.get("order_quantity", 0)):
        reasons.append("INSUFFICIENT_AVAILABLE_POSITION")
    if intent.side == "SELL" and context.get("t1_locked"):
        reasons.append("T_PLUS_ONE_LOCKED")
    if context.get("duplicate_order"):
        reasons.append("DUPLICATE_ORDER")
    if intent.strategy_id and context.get("strategy_status") not in {None, "ACTIVE"}:
        reasons.append("STRATEGY_NOT_ACTIVE")
    return reasons
