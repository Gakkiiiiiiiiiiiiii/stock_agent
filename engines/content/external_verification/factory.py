from __future__ import annotations

import os
from typing import Any

from engines.content.external_verification.base import make_result
from engines.content.external_verification.filing_provider import FilingVerificationProvider
from engines.content.external_verification.fundamental_provider import FundamentalVerificationProvider
from engines.content.external_verification.market_data_provider import MarketDataVerificationProvider
from engines.content.external_verification.policy_provider import PolicyVerificationProvider


class CompositeProvider:
    """按顺序尝试多个 provider 的组合路由器（§26 Provider Router）。

    路由由各 provider 的 supports() 决定：
    PRICE_LEVEL → market；VALUATION → market + fundamental；
    FINANCIAL_METRIC → fundamental；POLICY_FACT → policy；FACT → predicate 路由。
    第一个 supports() 命中的 provider 给出结果；全部不命中 -> NOT_FOUND。
    """

    def __init__(self, providers: list[Any]) -> None:
        self.providers = list(providers)

    def supports(self, unit: dict[str, Any]) -> bool:
        return any(provider.supports(unit) for provider in self.providers)

    def verify(self, unit: dict[str, Any]) -> dict[str, Any]:
        for provider in self.providers:
            if provider.supports(unit):
                return provider.verify(unit)
        return make_result("NOT_FOUND", provider="composite", reason="NO_PROVIDER_SUPPORTS")


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
