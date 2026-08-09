from __future__ import annotations

import logging
import os
import re
from datetime import date
from typing import Any

from engines.content.external_verification.base import claim_as_of, claim_numbers, extract_ticker, make_result

logger = logging.getLogger(__name__)

# §6.2：至少支持的指标集合。识别不出指标的 unit 不应进入本 provider（supports False）。
SUPPORTED_METRICS = {
    "PE",
    "PB",
    "PS",
    "EPS",
    "REVENUE",
    "PROFIT",
    "GROSS_MARGIN",
    "NET_MARGIN",
    "ROE",
}

# 从 QMT 桥 financial-data 命令一次性拉取的财务表（xtquant 表名）。
FINANCIAL_TABLES = ("PershareIndex", "Income", "Balance", "Capital")

# predicate_key → metric 关键词映射（小写、去除非字母数字后匹配）。
_PREDICATE_METRIC_EXACT = {
    "pe": "PE",
    "pb": "PB",
    "ps": "PS",
    "eps": "EPS",
    "roe": "ROE",
    "revenue": "REVENUE",
    "profit": "PROFIT",
    "grossmargin": "GROSS_MARGIN",
    "netmargin": "NET_MARGIN",
}

# 增长类 claim 关键词（同比/增长/增速/下滑/下降）。
_GROWTH_KEYWORDS = ("同比", "增长", "增速", "下滑", "下降")

# xtquant 财务行字段候选（JSON key 大小写不敏感匹配，按优先级排列）。
_EPS_FIELDS = ("s_fa_eps_basic", "s_fa_eps_diluted", "epsjb", "eps_basic")
_BPS_FIELDS = ("s_fa_bps", "bps")
_ORPS_FIELDS = ("s_fa_orps", "orps")
_REVENUE_FIELDS = ("totaloperaterevenue", "operaterevenue")
_COST_FIELDS = ("operatecost",)
_PROFIT_FIELDS = ("parentnetprofit", "netprofit")
_EQUITY_FIELDS = ("totalparentequity", "totalshareholderequity", "totalequity")
_TOTAL_CAPITAL_FIELDS = ("total_capital", "totalcapital")

_EPSILON = 1e-12


def _lower_keyed(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key).lower(): value for key, value in row.items()}


def _row_value(row: dict[str, Any], candidates: tuple[str, ...]) -> float | None:
    lowered = _lower_keyed(row)
    for field in candidates:
        value = lowered.get(field)
        if value is None or value == "":
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number != number:  # NaN
            continue
        return number
    return None


def _report_tag(row: dict[str, Any]) -> str:
    """报告期标签（m_timetag，YYYYMMDD），只保留数字。"""
    lowered = _lower_keyed(row)
    return re.sub(r"\D", "", str(lowered.get("m_timetag") or ""))


def _announce_tag(row: dict[str, Any]) -> str:
    lowered = _lower_keyed(row)
    return re.sub(r"\D", "", str(lowered.get("m_anntime") or ""))


def _sorted_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """按报告期倒序（最新在前）返回 dict 行。"""
    dict_rows = [row for row in rows or [] if isinstance(row, dict)]
    return sorted(dict_rows, key=lambda row: (_report_tag(row), _announce_tag(row)), reverse=True)


def _latest_with_field(rows: list[dict[str, Any]], candidates: tuple[str, ...]) -> tuple[dict[str, Any], float] | None:
    for row in _sorted_rows(rows):
        value = _row_value(row, candidates)
        if value is not None:
            return row, value
    return None


def _announced_by(rows: list[dict[str, Any]], as_of_tag: str) -> list[dict[str, Any]]:
    """剔除公告日晚于 claim as_of 的报告（§6.4：视频发布后才披露的财报不得使用）。"""
    kept: list[dict[str, Any]] = []
    for row in rows:
        announce = _announce_tag(row)
        if len(announce) >= 8 and announce[:8] > as_of_tag:
            continue
        kept.append(row)
    return kept


def _prior_year_row(rows: list[dict[str, Any]], reference_tag: str, candidates: tuple[str, ...]) -> tuple[dict[str, Any], float] | None:
    """找上一年同报告期的行（如 20241231 → 20231231）。"""
    if len(reference_tag) < 8:
        return None
    prior_tag = str(int(reference_tag[:4]) - 1) + reference_tag[4:8]
    for row in _sorted_rows(rows):
        if _report_tag(row) != prior_tag:
            continue
        value = _row_value(row, candidates)
        if value is not None:
            return row, value
    return None


class FundamentalVerificationProvider:
    """基本面/财务指标验证 Provider（§26：FINANCIAL_METRIC/VALUATION → fundamental data）。

    数据源为项目现有 QMT 桥 financial-data 命令（xtquant 财务表）：
    - 财报侧只使用公告日 <= claim as_of_time 的报告（end_date=as_of, report_type=announce_time）；
    - PE/PB/PS 由 <= as_of 的收盘价 + <= as_of 公告的每股指标计算（close 经 get_kline 时点过滤），
      任一侧拿不到即 NOT_FOUND，禁止「历史财报 + 当前股价」混搭；
    - EPS/REVENUE/PROFIT/GROSS_MARGIN/NET_MARGIN/ROE 直接取数或由报表字段计算；
    - 增长类 claim（净利润同比增长20%）按上一年同报告期算同比增速比对，
      拿不到上期数据返回 NOT_FOUND 并注明，绝不静默 MATCH。

    - 桥客户端惰性加载：worker 环境若 QMT 不可用，verify() 返回 ERROR。
    - 测试可注入 fake bridge（get_financial_data -> {symbol: {table: [rows]}}）
      与 fake market client（get_kline -> 带 records 的对象/dict）。
    - 容差与 market provider 一致（VIDEO_EXTERNAL_VERIFY_TOLERANCE，默认 ±5%）。
    """

    def __init__(
        self,
        bridge_client: Any | None = None,
        market_client: Any | None = None,
        tolerance: float | None = None,
    ) -> None:
        self._bridge_client = bridge_client
        self._market_client = market_client
        if tolerance is None:
            try:
                tolerance = float(os.getenv("VIDEO_EXTERNAL_VERIFY_TOLERANCE", "0.05"))
            except ValueError:
                tolerance = 0.05
        self.tolerance = max(0.0, tolerance)

    def supports(self, unit: dict[str, Any]) -> bool:
        kind = str(unit.get("knowledge_kind") or "").upper()
        if kind not in {"VALUATION", "FINANCIAL_METRIC"}:
            return False
        if extract_ticker(unit) is None:
            return False
        # 识别不出支持的 metric 时让 composite 落 NOT_FOUND，避免误判（§6.5）。
        return self._detect_metric(unit) in SUPPORTED_METRICS

    def verify(self, unit: dict[str, Any]) -> dict[str, Any]:
        ticker = extract_ticker(unit)
        if ticker is None:
            return make_result("NOT_FOUND", source_type="FUNDAMENTAL", provider="fundamental", reason="NO_TICKER")
        metric = self._detect_metric(unit)
        if metric not in SUPPORTED_METRICS:
            return make_result(
                "NOT_FOUND", source_type="FUNDAMENTAL", provider="fundamental", source_id=ticker, reason="METRIC_NOT_RECOGNIZED"
            )
        # §6.3：VALUATION/FINANCIAL_METRIC 属时点敏感类型，缺失 as_of_time 时不得默认用今天。
        as_of = claim_as_of(unit)
        if as_of is None:
            return make_result(
                "NOT_FOUND",
                source_type="FUNDAMENTAL",
                provider="fundamental",
                source_id=ticker,
                reason="AS_OF_TIME_MISSING",
                provenance={"metric": metric, "claim_as_of": None},
            )
        claim = self._claim_value(unit, metric)
        if claim is None:
            return make_result(
                "NOT_FOUND", source_type="FUNDAMENTAL", provider="fundamental", source_id=ticker, reason="NO_CLAIM_NUMBER"
            )
        claim_value, claim_unit = claim
        statement = str(unit.get("statement") or "")
        is_growth = (
            claim_unit == "PERCENT"
            and metric in {"REVENUE", "PROFIT"}
            and any(keyword in statement for keyword in _GROWTH_KEYWORDS)
        )

        try:
            tables_map = self._fetch_tables(ticker, as_of=as_of)
        except Exception as exc:  # worker 环境优雅降级，绝不把不可用当 MATCH
            logger.warning("财务数据获取失败 ticker=%s metric=%s: %s", ticker, metric, exc)
            return make_result(
                "ERROR",
                source_type="FUNDAMENTAL",
                provider="fundamental",
                source_id=ticker,
                reason="FUNDAMENTAL_DATA_UNAVAILABLE",
                provenance={"error": str(exc), "metric": metric},
            )
        if not tables_map:
            return make_result(
                "NOT_FOUND", source_type="FUNDAMENTAL", provider="fundamental", source_id=ticker, reason="NO_FUNDAMENTAL_DATA"
            )
        # §6.4：公告日晚于 claim as_of 的报告一律剔除（增长类两期同样受限）。
        as_of_tag = as_of.strftime("%Y%m%d")
        tables_map = {table: _announced_by(rows, as_of_tag) for table, rows in tables_map.items()}

        observed, result_unit, result_as_of, miss_reason, extra = self._compute_observed(ticker, metric, tables_map, is_growth, as_of)
        if observed is None:
            return make_result(
                "NOT_FOUND",
                source_type="FUNDAMENTAL",
                provider="fundamental",
                source_id=ticker,
                as_of=result_as_of,
                reason=miss_reason or "METRIC_NOT_COMPUTABLE",
                provenance={"metric": metric, "claim_as_of": as_of.isoformat(), **extra},
            )

        # 金额类 claim 按 claim 单位换算观测值（元 → 亿/万）。
        if metric in {"REVENUE", "PROFIT"} and not is_growth:
            if claim_unit == "CNY_YI":
                observed, result_unit = observed / 1e8, "CNY_100M"
            elif claim_unit == "CNY_WAN":
                observed, result_unit = observed / 1e4, "CNY_WAN"

        matched = abs(claim_value - observed) <= self.tolerance * max(abs(observed), _EPSILON)
        return make_result(
            "MATCH" if matched else "CONFLICT",
            source_type="FUNDAMENTAL",
            provider="fundamental",
            source_id=ticker,
            as_of=result_as_of,
            observed_value=round(observed, 6),
            unit=result_unit,
            provenance={
                "metric": metric,
                "claim_value": claim_value,
                "claim_unit": claim_unit,
                "is_growth": is_growth,
                "tolerance": self.tolerance,
                "claim_as_of": as_of.isoformat(),
                "external_data_as_of": result_as_of,
                **extra,
            },
        )

    # ---------- claim 解析 ----------

    def _detect_metric(self, unit: dict[str, Any]) -> str | None:
        predicate = re.sub(r"[^a-z0-9]", "", str(unit.get("predicate_key") or "").lower())
        if predicate in _PREDICATE_METRIC_EXACT:
            return _PREDICATE_METRIC_EXACT[predicate]
        for token, metric in _PREDICATE_METRIC_EXACT.items():
            if len(token) >= 3 and predicate.endswith(token):
                return metric
        statement = str(unit.get("statement") or "")
        try:
            from engines.content.financial_numeric import parse_financial_numerics

            for item in parse_financial_numerics(statement):
                metric = getattr(item, "metric", None) or (item.get("metric") if isinstance(item, dict) else None)
                if metric and str(metric).upper() in SUPPORTED_METRICS:
                    return str(metric).upper()
        except Exception:
            pass
        return None

    def _claim_value(self, unit: dict[str, Any], metric: str) -> tuple[float, str | None] | None:
        statement = str(unit.get("statement") or "")
        try:
            from engines.content.financial_numeric import parse_financial_numerics

            fallback: tuple[float, str | None] | None = None
            for item in parse_financial_numerics(statement):
                if isinstance(item, dict):
                    value, item_unit, item_metric = item.get("value"), item.get("unit"), item.get("metric")
                else:
                    value, item_unit, item_metric = (
                        getattr(item, "value", None),
                        getattr(item, "unit", None),
                        getattr(item, "metric", None),
                    )
                if value is None:
                    continue
                if item_metric and str(item_metric).upper() == metric:
                    return float(value), item_unit
                if fallback is None:
                    fallback = (float(value), item_unit)
            if fallback is not None:
                return fallback
        except Exception:
            pass
        numbers = claim_numbers(statement)
        return (numbers[0], None) if numbers else None

    # ---------- 数据获取与指标计算 ----------

    def _bridge(self) -> Any:
        if self._bridge_client is not None:
            return self._bridge_client
        # 惰性 import：避免 worker 环境在模块加载期就拉起 QMT 桥。
        from engines.market.qmt_bridge_client import QmtBridgeClient

        self._bridge_client = QmtBridgeClient()
        return self._bridge_client

    def _market(self) -> Any:
        if self._market_client is not None:
            return self._market_client
        from engines.market.data_provider import get_market_data_provider

        self._market_client = get_market_data_provider()
        return self._market_client

    def _fetch_tables(self, ticker: str, as_of: date | None = None) -> dict[str, list[dict[str, Any]]]:
        from engines.market.data_provider import to_qmt_symbol

        symbol = to_qmt_symbol(ticker)
        data = self._bridge().get_financial_data(
            [symbol],
            list(FINANCIAL_TABLES),
            end_date=as_of.strftime("%Y%m%d") if as_of is not None else None,
            report_type="announce_time",
        ) or {}
        table_map = data.get(symbol) or data.get(ticker) or {}
        return {str(table): list(rows or []) for table, rows in table_map.items()}

    def _table(self, tables_map: dict[str, list[dict[str, Any]]], name: str) -> list[dict[str, Any]]:
        for key, rows in tables_map.items():
            if key.lower() == name.lower():
                return rows
        return []

    def _latest_close(self, ticker: str, as_of: date | None = None) -> tuple[float | None, str | None]:
        from engines.content.external_verification.market_data_provider import MarketDataVerificationProvider

        # §6.5：PE/PB/PS 价格侧必须使用 <= as_of 的收盘价，禁止「历史财报 + 当前股价」混搭。
        return MarketDataVerificationProvider._latest_close(self._market(), ticker, as_of=as_of)

    def _compute_observed(
        self,
        ticker: str,
        metric: str,
        tables_map: dict[str, list[dict[str, Any]]],
        is_growth: bool,
        as_of: date | None = None,
    ) -> tuple[float | None, str | None, str | None, str | None, dict[str, Any]]:
        """返回 (observed, unit, as_of, miss_reason, extra_provenance)。"""
        income_rows = self._table(tables_map, "Income")
        pershare_rows = self._table(tables_map, "PershareIndex")

        if is_growth:
            fields = _PROFIT_FIELDS if metric == "PROFIT" else _REVENUE_FIELDS
            latest = _latest_with_field(income_rows, fields)
            if latest is None:
                return None, None, None, "NO_FUNDAMENTAL_DATA", {}
            latest_row, current = latest
            prior = _prior_year_row(income_rows, _report_tag(latest_row), fields)
            as_of = _announce_tag(latest_row) or _report_tag(latest_row) or None
            if prior is None:
                return None, None, as_of, "NO_PRIOR_PERIOD_DATA", {"current_value": current}
            _, previous = prior
            if abs(previous) <= _EPSILON:
                return None, None, as_of, "NO_PRIOR_PERIOD_DATA", {"current_value": current}
            growth = (current - previous) / abs(previous) * 100.0
            return growth, "PERCENT", as_of, None, {"current_value": current, "prior_value": previous}

        if metric == "EPS":
            found = _latest_with_field(pershare_rows, _EPS_FIELDS)
            if found is None:
                return None, None, None, "METRIC_NOT_COMPUTABLE", {"missing": "eps"}
            row, eps = found
            return eps, "CNY", _announce_tag(row) or _report_tag(row) or None, None, {}

        if metric in {"PE", "PB", "PS"}:
            close, close_as_of = self._latest_close(ticker, as_of=as_of)
            if close is None or close <= 0:
                return None, None, None, "NO_MARKET_DATA", {}
            if metric == "PE":
                found = _latest_with_field(pershare_rows, _EPS_FIELDS)
                missing = "eps"
            elif metric == "PB":
                found = _latest_with_field(pershare_rows, _BPS_FIELDS)
                missing = "bps"
            else:
                found = _latest_with_field(pershare_rows, _ORPS_FIELDS)
                missing = "orps"
                if found is None:
                    found = self._revenue_per_share(tables_map)
            if found is None:
                return None, None, close_as_of, "METRIC_NOT_COMPUTABLE", {"missing": missing, "close": close}
            row, per_share = found
            if per_share <= 0:
                # 亏损/负净资产时 PE/PB 无意义，不强行比对。
                return None, None, close_as_of, "NONPOSITIVE_PER_SHARE", {"per_share": per_share, "close": close}
            as_of = _announce_tag(row) or _report_tag(row) or close_as_of or None
            return close / per_share, "MULTIPLE", as_of, None, {"close": close, "per_share": per_share}

        if metric == "REVENUE":
            found = _latest_with_field(income_rows, _REVENUE_FIELDS)
            if found is None:
                return None, None, None, "METRIC_NOT_COMPUTABLE", {"missing": "revenue"}
            row, value = found
            return value, "CNY", _announce_tag(row) or _report_tag(row) or None, None, {}

        if metric == "PROFIT":
            found = _latest_with_field(income_rows, _PROFIT_FIELDS)
            if found is None:
                return None, None, None, "METRIC_NOT_COMPUTABLE", {"missing": "profit"}
            row, value = found
            return value, "CNY", _announce_tag(row) or _report_tag(row) or None, None, {}

        if metric == "GROSS_MARGIN":
            revenue = _latest_with_field(income_rows, _REVENUE_FIELDS)
            cost = _latest_with_field(income_rows, _COST_FIELDS)
            if revenue is None or cost is None or abs(revenue[1]) <= _EPSILON:
                return None, None, None, "METRIC_NOT_COMPUTABLE", {"missing": "gross_margin_fields"}
            row, revenue_value = revenue
            margin = (revenue_value - cost[1]) / abs(revenue_value) * 100.0
            return margin, "PERCENT", _announce_tag(row) or _report_tag(row) or None, None, {}

        if metric == "NET_MARGIN":
            revenue = _latest_with_field(income_rows, _REVENUE_FIELDS)
            profit = _latest_with_field(income_rows, _PROFIT_FIELDS)
            if revenue is None or profit is None or abs(revenue[1]) <= _EPSILON:
                return None, None, None, "METRIC_NOT_COMPUTABLE", {"missing": "net_margin_fields"}
            row, revenue_value = revenue
            margin = profit[1] / abs(revenue_value) * 100.0
            return margin, "PERCENT", _announce_tag(row) or _report_tag(row) or None, None, {}

        if metric == "ROE":
            profit = _latest_with_field(income_rows, _PROFIT_FIELDS)
            equity = _latest_with_field(self._table(tables_map, "Balance"), _EQUITY_FIELDS)
            if profit is None or equity is None or abs(equity[1]) <= _EPSILON:
                return None, None, None, "METRIC_NOT_COMPUTABLE", {"missing": "roe_fields"}
            row, profit_value = profit
            roe = profit_value / abs(equity[1]) * 100.0
            return roe, "PERCENT", _announce_tag(row) or _report_tag(row) or None, None, {}

        return None, None, None, "METRIC_NOT_RECOGNIZED", {}

    def _revenue_per_share(
        self, tables_map: dict[str, list[dict[str, Any]]]
    ) -> tuple[dict[str, Any], float] | None:
        """每股营收缺失时，用 营业收入 / 总股本 估算 PS 分母。"""
        revenue = _latest_with_field(self._table(tables_map, "Income"), _REVENUE_FIELDS)
        capital = _latest_with_field(self._table(tables_map, "Capital"), _TOTAL_CAPITAL_FIELDS)
        if revenue is None or capital is None or capital[1] <= 0:
            return None
        return revenue[0], revenue[1] / capital[1]
