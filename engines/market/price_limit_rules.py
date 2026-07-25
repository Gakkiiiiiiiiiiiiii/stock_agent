from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

MAIN_BOARD_ST_10_EFFECTIVE_DATE = date(2026, 7, 6)


@dataclass(frozen=True)
class PriceLimitRule:
    board: str
    limit_up_pct: float
    limit_down_pct: float
    has_price_limit: bool = True
    source: str = "rule"
    version: str = ""


def code_of(symbol: str) -> str:
    return str(symbol).split(".")[0].strip()


def board_of(symbol: str) -> str:
    code = code_of(symbol)
    if code.startswith("688"):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith("920") or code.startswith(("8", "4")):
        return "北交所"
    return "主板"


def round_to_tick(price: float, tick_size: float = 0.01) -> float:
    tick = Decimal(str(tick_size))
    units = (Decimal(str(price)) / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float((units * tick).quantize(tick, rounding=ROUND_HALF_UP))


def resolve_price_limit_rule(
    symbol: str,
    trade_date: date | datetime | str,
    *,
    quote: dict[str, Any] | None = None,
    security_meta: dict[str, Any] | None = None,
    is_risk_warning: bool | None = None,
    listing_stage: str | None = None,
) -> PriceLimitRule:
    if trade_date is None:
        raise ValueError("trade_date is required to resolve historical price-limit rules")
    day = _date_value(trade_date)
    payload = {**(security_meta or {}), **(quote or {})}
    board = board_of(symbol)
    stage = str(listing_stage or payload.get("listing_stage") or payload.get("trade_status") or "").upper()
    if stage in {"IPO_FIRST_DAY", "RELISTING_FIRST_DAY", "NO_LIMIT", "NONE_LIMIT"}:
        return PriceLimitRule(board=board, limit_up_pct=float("inf"), limit_down_pct=float("inf"), has_price_limit=False, source="listing_stage", version=stage)

    quote_rule = _quote_limit_rule(board, payload)
    if quote_rule is not None:
        return quote_rule

    risk_warning = _risk_warning(payload) if is_risk_warning is None else bool(is_risk_warning)
    if board == "科创板":
        return PriceLimitRule(board=board, limit_up_pct=0.20, limit_down_pct=0.20, version="STAR_20")
    if board == "创业板":
        return PriceLimitRule(board=board, limit_up_pct=0.20, limit_down_pct=0.20, version="CHINEXT_20")
    if board == "北交所":
        return PriceLimitRule(board=board, limit_up_pct=0.30, limit_down_pct=0.30, version="BSE_30")
    if risk_warning and day < MAIN_BOARD_ST_10_EFFECTIVE_DATE:
        return PriceLimitRule(board=board, limit_up_pct=0.05, limit_down_pct=0.05, version="MAIN_BOARD_ST_PRE_20260706")
    if risk_warning:
        return PriceLimitRule(board=board, limit_up_pct=0.10, limit_down_pct=0.10, version="MAIN_BOARD_ST_FROM_20260706")
    return PriceLimitRule(board=board, limit_up_pct=0.10, limit_down_pct=0.10, version="MAIN_BOARD_10")


def _quote_limit_rule(board: str, payload: dict[str, Any]) -> PriceLimitRule | None:
    up = _first_float(payload, "limit_up_rate", "LimitUpRate", "up_limit_rate", "涨停幅度")
    down = _first_float(payload, "limit_down_rate", "LimitDownRate", "down_limit_rate", "跌停幅度")
    if up is None and down is None:
        return None
    up = _normalize_rate(up if up is not None else down)
    down = _normalize_rate(down if down is not None else up)
    return PriceLimitRule(board=board, limit_up_pct=up, limit_down_pct=down, source="quote", version="QUOTE_LIMIT_RATE")


def _normalize_rate(value: float) -> float:
    value = abs(float(value))
    return value / 100.0 if value > 1 else value


def _first_float(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key not in payload or payload[key] in (None, ""):
            continue
        try:
            return float(str(payload[key]).replace("%", "").replace(",", ""))
        except ValueError:
            continue
    return None


def _risk_warning(payload: dict[str, Any]) -> bool:
    text = str(payload.get("name") or payload.get("stock_name") or payload.get("instrument_name") or "")
    return "ST" in text.upper() or "＊ST" in text.upper() or "*ST" in text.upper()


def _date_value(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).split(" ")[0].replace("/", "-")
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return date.fromisoformat(text)


__all__ = [
    "MAIN_BOARD_ST_10_EFFECTIVE_DATE",
    "PriceLimitRule",
    "board_of",
    "code_of",
    "resolve_price_limit_rule",
    "round_to_tick",
]
