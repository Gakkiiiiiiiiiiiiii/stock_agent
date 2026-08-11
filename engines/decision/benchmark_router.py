"""基准路由：为决策评估选择合适的比较基准（Decision Evaluation v2）。

输入：{decision_type, symbols, themes, market, style, sector}
输出：{primary_benchmark, style_benchmark, sector_benchmark, theme_benchmark, reason, router_version}

路由规则（按顺序评估，命中即确定 primary_benchmark）：
1. 显式 sector 映射（config/benchmark_router.yaml 的 sector_map，A 股中证全指一级行业指数代理）；
2. style 映射（growth→创业板指、dividend/value→中证红利、小盘/微盘→中证1000/2000 等）；
3. decision_type 映射（择时/资产配置类→沪深300）；
4. 默认基准（market_defaults[market] 或 000001.SH 上证指数）。

style/sector 基准腿无论是否命中主基准都会输出（供超额分解），theme 基准在
decision 带 themes 时输出特殊值 THEME_BASKET（决策候选等权组合）。
"""
from __future__ import annotations

from typing import Any

from financial_agent.config import load_yaml_config

THEME_BASKET = "THEME_BASKET"
DEFAULT_BENCHMARK = "000001.SH"
ROUTER_VERSION_FALLBACK = "benchmark_router_v1"


class BenchmarkRouter:
    """Config-driven benchmark routing for decision outcome evaluation."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        if config is None:
            try:
                config = load_yaml_config("benchmark_router.yaml").get("benchmark_router") or {}
            except FileNotFoundError:
                config = {}
        self.config = dict(config)

    def route(self, attributes: dict[str, Any] | None = None, **kwargs: Any) -> dict:
        attrs = {**(attributes or {}), **{k: v for k, v in kwargs.items() if v is not None}}
        cfg = self.config
        version = str(cfg.get("version") or ROUTER_VERSION_FALLBACK)
        market = str(attrs.get("market") or "CN_A")
        default = str((cfg.get("market_defaults") or {}).get(market) or cfg.get("default_benchmark") or DEFAULT_BENCHMARK)
        sector_entry = self._lookup(cfg.get("sector_map"), attrs.get("sector"))
        style_entry = self._lookup(cfg.get("style_map"), attrs.get("style"))
        sector_benchmark = sector_entry["benchmark"] if sector_entry else None
        style_benchmark = style_entry["benchmark"] if style_entry else None
        theme_benchmark = str(cfg.get("theme_basket_marker") or THEME_BASKET) if attrs.get("themes") else None

        if sector_entry is not None:
            primary, reason = sector_entry["benchmark"], f"sector:{attrs.get('sector')}→{sector_entry['name']}({sector_entry['benchmark']})"
        elif style_entry is not None:
            primary, reason = style_entry["benchmark"], f"style:{attrs.get('style')}→{style_entry['name']}({style_entry['benchmark']})"
        else:
            type_entry = self._lookup(cfg.get("decision_type_map"), attrs.get("decision_type"))
            if type_entry is not None:
                primary, reason = type_entry["benchmark"], f"decision_type:{attrs.get('decision_type')}→{type_entry['name']}({type_entry['benchmark']})"
            else:
                primary, reason = default, f"default:{market}市场默认基准({default})"
        return {
            "primary_benchmark": primary,
            "style_benchmark": style_benchmark,
            "sector_benchmark": sector_benchmark,
            "theme_benchmark": theme_benchmark,
            "reason": reason,
            "router_version": version,
        }

    @staticmethod
    def _lookup(mapping: Any, key: Any) -> dict | None:
        if not isinstance(mapping, dict) or key is None:
            return None
        entry = mapping.get(str(key))
        if isinstance(entry, str):
            return {"benchmark": entry, "name": entry}
        if isinstance(entry, dict) and entry.get("benchmark"):
            return {"benchmark": str(entry["benchmark"]), "name": str(entry.get("name") or entry["benchmark"])}
        return None
