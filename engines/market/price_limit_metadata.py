from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

UPPER_LIMIT_PRICE_KEYS = (
    "upper_limit_price",
    "UpperLimitPrice",
    "up_limit_price",
    "涨停价",
)
LOWER_LIMIT_PRICE_KEYS = (
    "lower_limit_price",
    "LowerLimitPrice",
    "down_limit_price",
    "跌停价",
)
LIMIT_UP_RATE_KEYS = (
    "limit_up_rate",
    "LimitUpRate",
    "up_limit_rate",
    "涨停幅度",
)
LIMIT_DOWN_RATE_KEYS = (
    "limit_down_rate",
    "LimitDownRate",
    "down_limit_rate",
    "跌停幅度",
)
RISK_WARNING_KEYS = (
    "is_risk_warning",
    "is_st",
    "risk_warning",
)
NAME_KEYS = (
    "name",
    "stock_name",
    "instrument_name",
)
LISTING_STAGE_KEYS = (
    "listing_stage",
    "trade_status",
)


class OptionalPriceStatus(str, Enum):
    MISSING = "MISSING"
    VALID = "VALID"
    INVALID = "INVALID"


@dataclass(frozen=True)
class ParsedOptionalPrice:
    status: OptionalPriceStatus
    value: float | None
    raw_value: object = None
    error: str | None = None


@dataclass(frozen=True)
class PriceLimitMetaCapabilities:
    buy_rule_meta: bool
    sell_rule_meta: bool
    actual_upper: bool
    actual_lower: bool
    rate_upper: bool
    rate_lower: bool
    risk_status: bool
    listing_stage: bool


def parse_optional_price(value: Any, field_name: str) -> ParsedOptionalPrice:
    if value in (None, ""):
        return ParsedOptionalPrice(
            status=OptionalPriceStatus.MISSING,
            value=None,
            raw_value=value,
        )
    try:
        price = float(value)
    except (TypeError, ValueError):
        return ParsedOptionalPrice(
            status=OptionalPriceStatus.INVALID,
            value=None,
            raw_value=value,
            error=f"PRICE_LIMIT_PRICE_INVALID:{field_name}:not_numeric",
        )
    if not math.isfinite(price) or price <= 0:
        return ParsedOptionalPrice(
            status=OptionalPriceStatus.INVALID,
            value=None,
            raw_value=value,
            error=f"PRICE_LIMIT_PRICE_INVALID:{field_name}:non_positive_or_non_finite",
        )
    return ParsedOptionalPrice(
        status=OptionalPriceStatus.VALID,
        value=price,
        raw_value=value,
    )


def first_present(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def extract_risk_warning(payload: dict[str, Any] | None) -> bool | None:
    data = dict(payload or {})
    explicit = first_present(data, RISK_WARNING_KEYS)
    if explicit is not None:
        if isinstance(explicit, str):
            return explicit.strip().lower() in {"1", "true", "yes", "y", "on", "st", "*st", "＊st"}
        return bool(explicit)
    name = first_present(data, NAME_KEYS)
    if name is None:
        return None
    normalized = str(name).upper()
    return "ST" in normalized or "*ST" in normalized or "＊ST" in normalized


def extract_listing_stage(payload: dict[str, Any] | None) -> str | None:
    value = first_present(dict(payload or {}), LISTING_STAGE_KEYS)
    return str(value).strip().upper() if value is not None else None


def extract_upper_limit_price(payload: dict[str, Any] | None) -> Any:
    return first_present(dict(payload or {}), UPPER_LIMIT_PRICE_KEYS)


def extract_lower_limit_price(payload: dict[str, Any] | None) -> Any:
    return first_present(dict(payload or {}), LOWER_LIMIT_PRICE_KEYS)


def extract_limit_up_rate(payload: dict[str, Any] | None) -> Any:
    return first_present(dict(payload or {}), LIMIT_UP_RATE_KEYS)


def extract_limit_down_rate(payload: dict[str, Any] | None) -> Any:
    return first_present(dict(payload or {}), LIMIT_DOWN_RATE_KEYS)


def _valid_rate_value(value: Any) -> bool:
    if value in (None, ""):
        return False
    try:
        rate = float(str(value).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return False
    if not math.isfinite(rate) or rate <= 0:
        return False
    if rate > 1:
        rate /= 100.0
    return rate <= 1


def inspect_price_limit_meta(
    meta: dict[str, Any] | None,
    *,
    upper_limit_price: Any = None,
    lower_limit_price: Any = None,
) -> PriceLimitMetaCapabilities:
    payload = dict(meta or {})
    upper = parse_optional_price(
        upper_limit_price if upper_limit_price is not None else extract_upper_limit_price(payload),
        "upper_limit_price",
    )
    lower = parse_optional_price(
        lower_limit_price if lower_limit_price is not None else extract_lower_limit_price(payload),
        "lower_limit_price",
    )
    rate_upper = _valid_rate_value(extract_limit_up_rate(payload))
    rate_lower = _valid_rate_value(extract_limit_down_rate(payload))
    risk_status = extract_risk_warning(payload) is not None
    listing_stage = extract_listing_stage(payload) is not None
    return PriceLimitMetaCapabilities(
        buy_rule_meta=upper.status is OptionalPriceStatus.VALID or rate_upper or risk_status or listing_stage,
        sell_rule_meta=lower.status is OptionalPriceStatus.VALID or rate_lower or risk_status or listing_stage,
        actual_upper=upper.status is OptionalPriceStatus.VALID,
        actual_lower=lower.status is OptionalPriceStatus.VALID,
        rate_upper=rate_upper,
        rate_lower=rate_lower,
        risk_status=risk_status,
        listing_stage=listing_stage,
    )


__all__ = [
    "OptionalPriceStatus",
    "ParsedOptionalPrice",
    "PriceLimitMetaCapabilities",
    "parse_optional_price",
    "first_present",
    "extract_risk_warning",
    "extract_listing_stage",
    "extract_upper_limit_price",
    "extract_lower_limit_price",
    "extract_limit_up_rate",
    "extract_limit_down_rate",
    "inspect_price_limit_meta",
    "UPPER_LIMIT_PRICE_KEYS",
    "LOWER_LIMIT_PRICE_KEYS",
    "LIMIT_UP_RATE_KEYS",
    "LIMIT_DOWN_RATE_KEYS",
    "RISK_WARNING_KEYS",
    "NAME_KEYS",
    "LISTING_STAGE_KEYS",
]
