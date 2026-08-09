from __future__ import annotations

import os
from typing import Any

from engines.content.external_verification.base import make_result
from engines.content.external_verification.filing_provider import FILING_PREDICATES, FilingVerificationProvider
from engines.content.external_verification.fundamental_provider import FundamentalVerificationProvider
from engines.content.external_verification.market_data_provider import PRICE_PREDICATES, MarketDataVerificationProvider
from engines.content.external_verification.policy_provider import PolicyVerificationProvider


class CompositeProvider:
    """metric-aware 路由器（§26 Provider Router / §6.5）。

    按 knowledge_kind 显式路由，不再「第一个 supports 命中即返回」：
    - PRICE_LEVEL → market；
    - VALUATION / FINANCIAL_METRIC → fundamental（避免 PE 被拿去和 close 比对）；
    - POLICY_FACT → policy；
    - FACT → predicate 路由（price 类 → market，公告类 → filing，其余 → NOT_FOUND）。
    目标 provider 缺失或 supports() 不命中 -> NOT_FOUND(NO_PROVIDER_SUPPORTS)。
    """

    def __init__(self, providers: list[Any]) -> None:
        self.providers = list(providers)
        self.market = self._find(MarketDataVerificationProvider)
        self.fundamental = self._find(FundamentalVerificationProvider)
        self.filing = self._find(FilingVerificationProvider)
        self.policy = self._find(PolicyVerificationProvider)

    def _find(self, provider_type: type) -> Any | None:
        for provider in self.providers:
            if isinstance(provider, provider_type):
                return provider
        return None

    def supports(self, unit: dict[str, Any]) -> bool:
        return any(provider.supports(unit) for provider in self.providers)

    def verify(self, unit: dict[str, Any]) -> dict[str, Any]:
        kind = str(unit.get("knowledge_kind") or "").upper()
        if kind == "PRICE_LEVEL":
            return self._verify_with(self.market, unit)
        if kind in {"VALUATION", "FINANCIAL_METRIC"}:
            return self._verify_with(self.fundamental, unit)
        if kind == "POLICY_FACT":
            return self._verify_with(self.policy, unit)
        if kind == "FACT":
            predicate = str(unit.get("predicate_key") or "").strip().lower()
            if predicate in PRICE_PREDICATES:
                return self._verify_with(self.market, unit)
            if predicate in FILING_PREDICATES:
                return self._verify_with(self.filing, unit)
            return make_result("NOT_FOUND", provider="composite", reason="NO_PROVIDER_SUPPORTS")
        return make_result("NOT_FOUND", provider="composite", reason="NO_PROVIDER_SUPPORTS")

    @staticmethod
    def _verify_with(provider: Any | None, unit: dict[str, Any]) -> dict[str, Any]:
        if provider is None or not provider.supports(unit):
            return make_result("NOT_FOUND", provider="composite", reason="NO_PROVIDER_SUPPORTS")
        return provider.verify(unit)


def build_default_provider() -> CompositeProvider | None:
    """构建默认 External Verification Provider（P0-7 / §25-26）。

    仅当 VIDEO_EXTERNAL_FACT_VERIFICATION=1 时构建真实 provider 组合，
    否则返回 None（ExternalFactVerifier 走 NOT_RUN 路径，行为与旧版一致）。
    """
    enabled = os.getenv("VIDEO_EXTERNAL_FACT_VERIFICATION", "0").lower() in {"1", "true", "yes"}
    if not enabled:
        return None
    return CompositeProvider(
        [
            MarketDataVerificationProvider(),
            FundamentalVerificationProvider(),
            FilingVerificationProvider(),
            PolicyVerificationProvider(),
        ]
    )
