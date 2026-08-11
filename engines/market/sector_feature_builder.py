"""板块特征构建器：基于全量成分股（非抽样）计算板块特征与板块强度分。

数据访问通过鸭子类型 provider 注入：
- get_quotes(symbols) -> {symbol: payload}（payload 含 last_price/last_close/amount 等）
- get_kline(symbol) -> K线响应（对象.records / dict["records"] / 记录列表均可）

测试中可直接用普通 dict 驱动的假 provider。
"""
from __future__ import annotations

from datetime import date, datetime
from statistics import median
from typing import Any, Sequence

from engines.market.data_provider import batched, is_st_quote, safe_float
from engines.market.feature_normalizer import cross_sectional_percentile, winsorize
from engines.market.models import SectorComponents, SectorStrengthResult
from engines.market.price_limit_rules import resolve_price_limit_rule
from engines.market.stock_feature_builder import compute_stock_features

DEFAULT_FEATURE_VERSION = "sector_strength_v2"
DEFAULT_LOW_COVERAGE_THRESHOLD = 0.6
DEFAULT_MIN_HISTORY_RECORDS = 60

LOW_COVERAGE = "LOW_COVERAGE"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
NO_VALID_SYMBOLS = "NO_VALID_SYMBOLS"
AMOUNT_HISTORY_UNAVAILABLE = "AMOUNT_HISTORY_UNAVAILABLE"
COMPONENT_DATA_MISSING = "COMPONENT_DATA_MISSING"

_NEUTRAL_SCORE = 50.0


def _mean_present(*values: float | None) -> float | None:
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def _median_present(values: Sequence[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return float(median(present))


def _ratio(count: int, total: int) -> float | None:
    if total <= 0:
        return None
    return count / total


def _records_of(kline_payload: Any) -> list[Any]:
    if kline_payload is None:
        return []
    if isinstance(kline_payload, dict):
        return list(kline_payload.get("records") or [])
    if isinstance(kline_payload, (list, tuple)):
        return list(kline_payload)
    records = getattr(kline_payload, "records", None)
    return list(records or [])


def _quote_change_pct(payload: dict[str, Any]) -> float | None:
    last_price = safe_float(payload.get("last_price") or payload.get("price"))
    last_close = safe_float(payload.get("last_close") or payload.get("pre_close"))
    if last_price <= 0 or last_close <= 0:
        return None
    return (last_price - last_close) / last_close * 100


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


class SectorFeatureBuilder:
    def __init__(
        self,
        data_access: Any = None,
        low_coverage_threshold: float = DEFAULT_LOW_COVERAGE_THRESHOLD,
        min_history_records: int = DEFAULT_MIN_HISTORY_RECORDS,
    ) -> None:
        self.data_access = data_access
        self.low_coverage_threshold = low_coverage_threshold
        self.min_history_records = min_history_records

    def build_sector_features(
        self,
        membership: dict[str, list[str]],
        market_context: dict[str, Any] | None = None,
        sector_codes: dict[str, str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """对每个板块基于全量成分股计算原始特征，返回 {sector: feature_dict}。"""
        context = market_context or {}
        codes = sector_codes or {}
        results: dict[str, dict[str, Any]] = {}
        for sector in sorted(membership):
            symbols = sorted({str(symbol).strip() for symbol in membership[sector] if str(symbol).strip()})
            results[sector] = self._build_one(sector, symbols, context, codes.get(sector))
        return results

    def _build_one(
        self,
        sector: str,
        symbols: list[str],
        context: dict[str, Any],
        sector_code: str | None,
    ) -> dict[str, Any]:
        quotes = self._fetch_quotes(symbols)
        stock_features: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            stock_features[symbol] = compute_stock_features(self._fetch_kline(symbol))

        universe_size = len(symbols)
        valid_symbols = [
            symbol
            for symbol in symbols
            if _quote_change_pct(quotes.get(symbol) or {}) is not None
            or (stock_features[symbol]["record_count"] >= 2 and stock_features[symbol]["close"])
        ]
        valid_count = len(valid_symbols)
        coverage = valid_count / universe_size if universe_size else None

        quote_pcts = {symbol: _quote_change_pct(quotes.get(symbol) or {}) for symbol in valid_symbols}
        valid_quote_pcts = [pct for pct in quote_pcts.values() if pct is not None]
        features_of = lambda name: [stock_features[symbol][name] for symbol in valid_symbols]  # noqa: E731

        return_5d_median = _median_present(features_of("return_5d"))
        return_20d_median = _median_present(features_of("return_20d"))
        market_return_5d = context.get("market_return_5d")
        market_return_20d = context.get("market_return_20d")

        sector_amount = sum(
            safe_float((quotes.get(symbol) or {}).get("amount") or (quotes.get(symbol) or {}).get("turnover"))
            for symbol in valid_symbols
        )
        amount_now = sum(value for value in features_of("amount") if value is not None)
        amount_ma5 = sum(value for value in features_of("amount_ma5") if value is not None)
        if sector_amount <= 0:
            sector_amount = amount_now
        total_market_amount = context.get("total_market_amount")
        amount_percentile = _median_present(features_of("amount_percentile_120d"))

        limit_up_count = limit_down_count = big_up_count = big_down_count = 0
        trade_day = context.get("trade_date") or date.today()
        for symbol, pct in quote_pcts.items():
            if pct is None:
                continue
            if pct > 5:
                big_up_count += 1
            elif pct < -5:
                big_down_count += 1
            payload = quotes.get(symbol) or {}
            try:
                rule = resolve_price_limit_rule(symbol, trade_day, quote=payload, is_risk_warning=is_st_quote(payload))
            except Exception:  # noqa: BLE001 - 涨跌停规则解析失败不影响主流程
                continue
            if not rule.has_price_limit or rule.limit_up_pct is None or rule.limit_down_pct is None:
                continue
            change = pct / 100
            if change >= rule.limit_up_pct - 0.002:
                limit_up_count += 1
            elif change <= -rule.limit_down_pct + 0.002:
                limit_down_count += 1

        record_counts = [stock_features[symbol]["record_count"] for symbol in valid_symbols]
        quality_flags: list[str] = []
        if coverage is not None and coverage < self.low_coverage_threshold:
            quality_flags.append(LOW_COVERAGE)
        if valid_count == 0:
            quality_flags.append(NO_VALID_SYMBOLS)
        median_records = _median_present([float(count) for count in record_counts])
        if median_records is not None and median_records < self.min_history_records:
            quality_flags.append(INSUFFICIENT_HISTORY)
        if amount_percentile is None:
            quality_flags.append(AMOUNT_HISTORY_UNAVAILABLE)

        return {
            "sector": sector,
            "sector_code": sector_code,
            "universe_size": universe_size,
            "valid_symbol_count": valid_count,
            "coverage": round(coverage, 6) if coverage is not None else None,
            # 价格强度
            "return_1d_median": _median_present(features_of("return_1d")),
            "return_5d_median": return_5d_median,
            "return_20d_median": return_20d_median,
            "relative_return_5d": None if return_5d_median is None or market_return_5d is None else return_5d_median - float(market_return_5d),
            "relative_return_20d": None if return_20d_median is None or market_return_20d is None else return_20d_median - float(market_return_20d),
            # 宽度
            "up_ratio": _ratio(sum(1 for pct in valid_quote_pcts if pct > 0), len(valid_quote_pcts)),
            "above_ma20_ratio": _ratio(sum(1 for value in features_of("above_ma20") if value), sum(1 for value in features_of("above_ma20") if value is not None)),
            "above_ma60_ratio": _ratio(sum(1 for value in features_of("above_ma60") if value), sum(1 for value in features_of("above_ma60") if value is not None)),
            "new_high_20d_ratio": _ratio(sum(1 for value in features_of("new_high_20d") if value), sum(1 for value in features_of("new_high_20d") if value is not None)),
            "positive_5d_ratio": _ratio(sum(1 for value in features_of("return_5d") if value is not None and value > 0), sum(1 for value in features_of("return_5d") if value is not None)),
            # 热度/流动性
            "sector_amount": round(sector_amount, 2) if sector_amount > 0 else None,
            "amount_share": round(sector_amount / float(total_market_amount), 6) if sector_amount > 0 and total_market_amount else None,
            "amount_change_5d": round((amount_now / amount_ma5 - 1) * 100, 4) if amount_now > 0 and amount_ma5 > 0 else None,
            "amount_percentile_120d": amount_percentile,
            # 极端行为
            "limit_up_count": limit_up_count,
            "limit_down_count": limit_down_count,
            "big_up_count": big_up_count,
            "big_down_count": big_down_count,
            # 风险
            "volatility_20d": _median_present(features_of("volatility_20d")),
            "max_drawdown_20d": _median_present(features_of("max_drawdown_20d")),
            "quality_flags": quality_flags,
        }

    def compute_strength(
        self,
        sector_features: dict[str, dict[str, Any]],
        weights: dict[str, float],
        feature_version: str = DEFAULT_FEATURE_VERSION,
        as_of: datetime | None = None,
    ) -> list[SectorStrengthResult]:
        """横截面归一化（winsorize → percentile）后按权重合成 0-100 强度分。确定性输出。"""
        sectors = sorted(sector_features)
        raw_components = {sector: self._raw_components(sector_features[sector]) for sector in sectors}
        component_scores: dict[str, dict[str, float | None]] = {sector: {} for sector in sectors}
        for component in ("trend", "breadth", "relative_strength", "liquidity", "momentum", "risk_penalty"):
            raw_series = [raw_components[sector][component] for sector in sectors]
            smoothed = winsorize(raw_series)
            percentiles = cross_sectional_percentile(smoothed)
            for sector, score in zip(sectors, percentiles, strict=False):
                component_scores[sector][component] = None if score is None else round(score, 6)

        results: list[SectorStrengthResult] = []
        for sector in sectors:
            scores = component_scores[sector]
            flags = list(sector_features[sector].get("quality_flags") or [])
            weighted = 0.0
            for component in ("trend", "breadth", "relative_strength", "liquidity", "momentum"):
                score = scores[component]
                if score is None:
                    score = _NEUTRAL_SCORE
                    flags.append(COMPONENT_DATA_MISSING)
                weighted += float(weights.get(component, 0.0)) * score
            risk_score = scores["risk_penalty"]
            if risk_score is None:
                risk_score = _NEUTRAL_SCORE
                flags.append(COMPONENT_DATA_MISSING)
            weighted -= float(weights.get("risk_penalty", 0.0)) * risk_score
            feature = sector_features[sector]
            results.append(
                SectorStrengthResult(
                    sector=sector,
                    sector_code=feature.get("sector_code"),
                    strength_score=_clamp_score(weighted),
                    rank=None,
                    universe_size=int(feature.get("universe_size") or 0),
                    valid_symbol_count=int(feature.get("valid_symbol_count") or 0),
                    coverage=feature.get("coverage"),
                    components=SectorComponents(**scores),
                    as_of=as_of,
                    feature_version=feature_version,
                    quality_flags=sorted(set(flags)),
                )
            )
        results.sort(key=lambda item: (-(item.strength_score or 0.0), item.sector))
        for position, item in enumerate(results, start=1):
            item.rank = position
        return results

    @staticmethod
    def _raw_components(feature: dict[str, Any]) -> dict[str, float | None]:
        amount_change = feature.get("amount_change_5d")
        amount_percentile = feature.get("amount_percentile_120d")
        drawdown = feature.get("max_drawdown_20d")
        return {
            "trend": _mean_present(feature.get("return_5d_median"), feature.get("return_20d_median")),
            "breadth": _mean_present(
                feature.get("up_ratio"),
                feature.get("above_ma20_ratio"),
                feature.get("above_ma60_ratio"),
                feature.get("new_high_20d_ratio"),
                feature.get("positive_5d_ratio"),
            ),
            "relative_strength": _mean_present(feature.get("relative_return_5d"), feature.get("relative_return_20d")),
            "liquidity": _mean_present(
                feature.get("amount_share"),
                None if amount_change is None else float(amount_change) / 100.0,
                None if amount_percentile is None else float(amount_percentile) / 100.0,
            ),
            "momentum": feature.get("return_1d_median"),
            "risk_penalty": _mean_present(
                feature.get("volatility_20d"),
                None if drawdown is None else abs(float(drawdown)),
            ),
        }

    def _fetch_quotes(self, symbols: list[str]) -> dict[str, Any]:
        if self.data_access is None or not symbols:
            return {}
        quotes: dict[str, Any] = {}
        for chunk in batched(symbols, 200):
            try:
                quotes.update(self.data_access.get_quotes(chunk) or {})
            except Exception:  # noqa: BLE001 - 单批行情失败不阻断其他批次
                continue
        return quotes

    def _fetch_kline(self, symbol: str) -> list[Any]:
        if self.data_access is None:
            return []
        try:
            return _records_of(self.data_access.get_kline(symbol))
        except Exception:  # noqa: BLE001 - 单标的 K 线失败按缺失处理
            return []
