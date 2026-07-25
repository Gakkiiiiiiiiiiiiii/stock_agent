from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from engines.market.data_provider import get_market_data_provider


class MarketFeatureBuilder:
    def __init__(self, provider=None) -> None:
        self.provider = provider or get_market_data_provider()

    def build(self, as_of: datetime | None = None, force_refresh: bool = False) -> dict[str, Any]:
        snapshot_date = _as_date(as_of)
        snapshot = self.provider.get_market_snapshot(as_of=snapshot_date, force_refresh=force_refresh)
        sectors_payload = self.provider.get_sector_strength(as_of=snapshot_date)
        sectors = sectors_payload.get("sectors", sectors_payload) if isinstance(sectors_payload, dict) else sectors_payload
        top_theme_strength = max([float(item.get("strength_score") or 0.0) for item in sectors] or [None])
        indices = snapshot.get("indices") or {}
        index_return_5d = _decimal_or_none(snapshot["index_return_5d"]) if snapshot.get("index_return_5d") is not None else pct_to_decimal(indices.get("return_5d_pct"))
        index_return_20d = _decimal_or_none(snapshot["index_return_20d"]) if snapshot.get("index_return_20d") is not None else pct_to_decimal(indices.get("return_20d_pct"))
        index_drawdown_20d = _decimal_or_none(snapshot["index_drawdown_20d"]) if snapshot.get("index_drawdown_20d") is not None else pct_to_decimal(indices.get("drawdown_20d_pct"))
        limit_down_count = snapshot.get("limit_down_count")
        down_count = snapshot.get("down_count")
        up_count = snapshot.get("up_count")
        estimated_high_position = any(
            snapshot.get(name) is None
            for name in ("high_position_loss_ratio", "high_position_limit_down_ratio", "high_position_breakdown_ratio", "high_position_big_negative_count")
        )
        estimated_loss_ratio = _ratio(down_count, (up_count or 0) + (down_count or 0))
        estimated_limit_down_ratio = _ratio(limit_down_count, (snapshot.get("limit_up_count") or 0) + (limit_down_count or 0))
        estimated_breakdown_ratio = max(0.0, -(index_return_5d or 0.0))
        estimated_big_negative_count = _retreat_days(index_drawdown_20d)
        quality_flags = list(snapshot.get("quality_flags") or [])
        quality_flags.extend(snapshot.get("high_position_quality_flags") or [])
        if estimated_high_position:
            quality_flags.append("HIGH_POSITION_FEATURES_ESTIMATED")
        if snapshot.get("warning"):
            quality_flags.append("MARKET_FEATURES_INCOMPLETE")
        feature = {
            "as_of": as_of or datetime.now(timezone.utc),
            "universe_size": snapshot.get("universe_size"),
            "requested_quote_count": snapshot.get("requested_quote_count"),
            "received_quote_count": snapshot.get("received_quote_count"),
            "quote_coverage": snapshot.get("quote_coverage"),
            "up_count": up_count,
            "down_count": down_count,
            "limit_up_count": snapshot.get("limit_up_count"),
            "limit_down_count": limit_down_count,
            "above_ma20_ratio": snapshot.get("above_ma20_ratio"),
            "above_ma60_ratio": snapshot.get("above_ma60_ratio"),
            "median_return_5d": snapshot.get("median_return_5d"),
            "index_return_5d": index_return_5d,
            "index_return_20d": index_return_20d,
            "index_volatility_20d": _decimal_or_none(snapshot["index_volatility_20d"]) if snapshot.get("index_volatility_20d") is not None else pct_to_decimal(indices.get("volatility_20d_pct")),
            "turnover_amount": snapshot.get("turnover_amount") or snapshot.get("turnover"),
            "turnover_percentile_1y": snapshot.get("turnover_percentile_1y"),
            "top10_amount_share": snapshot.get("top10_amount_share"),
            "sector_dispersion": _sector_dispersion(sectors),
            "sector_rotation_speed": snapshot.get("sector_rotation_speed"),
            "high_position_loss_ratio": _value_or_none(snapshot.get("high_position_loss_ratio")),
            "high_position_limit_down_ratio": _value_or_none(snapshot.get("high_position_limit_down_ratio")),
            "high_position_breakdown_ratio": _value_or_none(snapshot.get("high_position_breakdown_ratio")),
            "high_position_big_negative_count": snapshot.get("high_position_big_negative_count") if snapshot.get("high_position_big_negative_count") is not None else None,
            "high_position_pool_size": snapshot.get("high_position_pool_size"),
            "high_position_valid_count": snapshot.get("high_position_valid_count"),
            "high_position_quote_coverage": snapshot.get("high_position_quote_coverage"),
            "high_position_quality_flags": list(snapshot.get("high_position_quality_flags") or []),
            "estimated_high_position_loss_ratio": estimated_loss_ratio,
            "estimated_high_position_limit_down_ratio": estimated_limit_down_ratio,
            "estimated_high_position_breakdown_ratio": estimated_breakdown_ratio,
            "estimated_high_position_big_negative_count": estimated_big_negative_count,
            "quality_score": snapshot.get("quality_score") if snapshot.get("quality_score") is not None else _quality_score(snapshot),
            "quality_flags": sorted(set(quality_flags)),
            "top_theme_strength": top_theme_strength,
            "index_drawdown_20d": index_drawdown_20d,
            "source": snapshot.get("source"),
            "warning": snapshot.get("warning"),
        }
        return feature


def _pct_to_decimal(value) -> float | None:
    return pct_to_decimal(value)


def pct_to_decimal(value) -> float | None:
    if value is None:
        return None
    return float(value) / 100.0


def _decimal_or_none(value) -> float | None:
    return None if value is None else float(value)


def _value_or_none(value) -> float | None:
    return None if value is None else float(value)


def _ratio(part, total) -> float | None:
    if part is None or not total:
        return None
    return round(max(0.0, min(1.0, float(part) / float(total))), 6)


def _sector_dispersion(sectors) -> float | None:
    values = [float(item.get("change_pct")) for item in sectors or [] if item.get("change_pct") is not None]
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return round(variance ** 0.5 / 100, 6)


def _retreat_days(drawdown) -> int | None:
    if drawdown is None:
        return None
    return int(min(10, max(0, round(abs(float(drawdown)) * 100))))


def _quality_score(snapshot: dict[str, Any]) -> float:
    if snapshot.get("warning"):
        return 0.0
    fields = ("up_count", "down_count", "limit_up_count", "limit_down_count", "indices")
    present = sum(1 for field in fields if snapshot.get(field) is not None)
    return round(present / len(fields), 4)


def _as_date(value: datetime | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value
