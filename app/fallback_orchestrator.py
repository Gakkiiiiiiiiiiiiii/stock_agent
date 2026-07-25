from __future__ import annotations

from datetime import date

import pandas as pd
import yaml

from engines.market.data_provider import get_market_data_provider
from engines.technical.profile_loader import load_technical_profile
from engines.technical.registry import default_indicator_registry
from engines.technical.rule_engine import RuleEngine
from engines.technical.rule_validator import RulePackValidator
from engines.theme.theme_score import rank_themes
from financial_agent.models import ThemeScoreInput
from financial_agent.utils import project_root
from storage.repositories.theme_repository import ThemeRepository


class LocalFallbackOrchestrator:
    def __init__(self) -> None:
        self.market = get_market_data_provider()
        self.themes = ThemeRepository()

    def analyze_stock(self, symbol: str, as_of: date | None = None, patterns: list[str] | None = None) -> dict:
        kline = self.market.get_kline(symbol, end_date=as_of)
        kline_guard = self._validate_kline_for_analysis(kline, as_of=as_of)
        if kline_guard is not None:
            return {"symbol": symbol, **kline_guard}
        profile = load_technical_profile("core_daily_v1")
        if len(kline.records) < profile.minimum_bars:
            return {
                "symbol": symbol,
                "error": {
                    "code": "INSUFFICIENT_BARS",
                    "required": profile.minimum_bars,
                    "actual": len(kline.records),
                },
            }
        registry = default_indicator_registry()
        frame = pd.DataFrame([item.model_dump() for item in kline.records])
        frame["return"] = frame["close"].pct_change()
        validation = registry.validate_profile(profile, fields=set(frame.columns))
        if not validation["valid"]:
            return {"symbol": symbol, "error": {"code": "TECHNICAL_PROFILE_INVALID", "details": validation["errors"]}}
        indicator_frame = pd.DataFrame(index=frame.index)
        latest_indicators: dict[str, float | None] = {}
        for spec in profile.indicators:
            result = registry.get(spec.name).calculate(frame, spec.params)
            if isinstance(result, pd.DataFrame):
                for column in result.columns:
                    key = f"{spec.alias}.{column}"
                    indicator_frame[key] = result[column]
                    value = result[column].iloc[-1]
                    latest_indicators[key] = None if pd.isna(value) else round(float(value), profile.output_precision)
            else:
                indicator_frame[spec.alias] = result
                value = result.iloc[-1]
                latest_indicators[spec.alias] = None if pd.isna(value) else round(float(value), profile.output_precision)
        rule_pack_name, rule_pack = self._load_rule_pack_for_profile(profile.name)
        RulePackValidator(registry).validate(rule_pack_name, rule_pack, profile)
        evaluations = []
        engine = RuleEngine()
        for rule in rule_pack.get("rules") or []:
            if rule.get("enabled", True):
                item = engine.evaluate_rule(rule, indicator_frame)
                evaluations.append(item)
        close = float(frame["close"].iloc[-1])
        return {
            "symbol": symbol,
            "date": str(kline.records[-1].date),
            "technical": {
                "close": close,
                "profile": {"name": profile.name, "version": profile.version, "hash": registry.fingerprint(profile)},
                "rule_pack": {"name": rule_pack_name, "version": str(rule_pack.get("version") or "1.0.0")},
                "indicators": latest_indicators,
                "rules": [
                    item.__dict__ | {"status": item.status.value}
                    for item in evaluations
                ],
                "score": round(sum(item.score_awarded for item in evaluations), 4),
            },
            "summary": self._stock_summary(evaluations),
            "risk": {"warnings": []},
            "orchestration": "local-fallback",
        }

    @staticmethod
    def _validate_kline_for_analysis(kline, as_of: date | None = None) -> dict | None:
        if not kline.records:
            return {
                "error": "QMT 未返回可用日 K 数据，已停止分析。",
                "data_source": kline.source,
                "warning": kline.warning,
            }
        latest_date = kline.records[-1].date
        reference_date = as_of or date.today()
        if kline.source != "qmt":
            return {
                "error": "当前返回的数据源不是 QMT 实时行情，已停止分析。",
                "data_source": kline.source,
                "latest_kline_date": str(latest_date),
                "warning": kline.warning,
            }
        if (reference_date - latest_date).days > 14:
            return {
                "error": "日 K 数据时间过旧，无法用于当前分析。",
                "data_source": kline.source,
                "latest_kline_date": str(latest_date),
                "warning": kline.warning,
            }
        return None

    def analyze_theme(self, theme_name: str) -> dict:
        theme = self.themes.search(theme_name)
        if not theme:
            return {"theme_name": theme_name, "exists": False, "summary": "知识库未找到该主题，可先补充核心逻辑、催化和证伪条件。"}
        score = rank_themes([ThemeScoreInput(theme=theme_name, knowledge_score=80, news_score=55, technical_score=55)])[0]
        return {"exists": True, "theme": theme.model_dump(), "score": score.model_dump(), "orchestration": "local-fallback"}

    def daily_scan(self, scan_date: date | None = None, mode: str = "after_close") -> dict:
        sectors = self.market.get_sector_strength()
        snapshot = self.market.get_market_snapshot()
        theme_scores = rank_themes(
            [
                ThemeScoreInput(theme=item["sector"], price_strength_score=item["strength_score"], technical_score=item["strength_score"], news_score=60)
                for item in sectors
            ]
        )
        return {
            "date": str(scan_date or date.today()),
            "mode": mode,
            "market_environment": {
                "market_regime": snapshot.get("market_regime"),
                "risk_appetite": snapshot.get("risk_appetite"),
                "suggested_position": "50%-70%",
                "warnings": [snapshot.get("warning")] if snapshot.get("warning") else [],
            },
            "top_themes": [item.model_dump() for item in theme_scores],
            "watch_points": ["观察成交额是否继续放大", "避免追高高位放量滞涨标的"],
            "orchestration": "local-fallback",
        }

    @staticmethod
    @staticmethod
    def _load_rule_pack_for_profile(profile_name: str) -> tuple[str, dict]:
        cfg = yaml.safe_load((project_root() / "config" / "technical_rule_packs.yaml").read_text(encoding="utf-8")) or {}
        for name, pack in (cfg.get("rule_packs") or {}).items():
            if pack.get("profile") == profile_name and str(pack.get("status") or "").lower() == "approved":
                return name, pack
        raise ValueError(f"approved rule pack not found for profile: {profile_name}")

    @staticmethod
    def _stock_summary(evaluations) -> str:
        triggered = [item for item in evaluations if item.status.value == "TRUE"]
        if not triggered:
            return "当前规则引擎未触发高置信技术信号，宜等待更明确确认。"
        names = "、".join(item.rule_id for item in triggered)
        return f"当前触发 {names}，需结合成交额、市场状态和证伪条件执行。"
