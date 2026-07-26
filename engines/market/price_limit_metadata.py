from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any


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
        upper_limit_price if upper_limit_price is not None else payload.get("upper_limit_price"),
        "upper_limit_price",
    )
    lower = parse_optional_price(
        lower_limit_price if lower_limit_price is not None else payload.get("lower_limit_price"),
        "lower_limit_price",
    )
    rate_upper = _valid_rate_value(
        payload.get("limit_up_rate")
        if payload.get("limit_up_rate") is not None
        else payload.get("LimitUpRate")
    )
    rate_lower = _valid_rate_value(
        payload.get("limit_down_rate")
        if payload.get("limit_down_rate") is not None
        else payload.get("LimitDownRate")
    )
    risk_status = (
        payload.get("is_risk_warning") is not None
        or bool(payload.get("name") or payload.get("stock_name") or payload.get("instrument_name"))
    )
    listing_stage = bool(payload.get("listing_stage"))
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
    "inspect_price_limit_meta",
]
