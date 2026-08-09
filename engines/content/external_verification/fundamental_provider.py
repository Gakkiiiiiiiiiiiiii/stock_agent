from __future__ import annotations

from typing import Any

from engines.content.external_verification.base import extract_ticker, make_result


class FundamentalVerificationProvider:
    """基本面/财务指标验证 Provider（§26：FINANCIAL_METRIC → financial statements）。

    占位实现：supports() 路由逻辑完整；verify() 暂返回 NOT_FOUND，
    待接入真实财务数据源（如 iFinD 财务接口 / 财报库）后填充数值比对。
    """

    def supports(self, unit: dict[str, Any]) -> bool:
        kind = str(unit.get("knowledge_kind") or "").upper()
        if kind == "FINANCIAL_METRIC":
            return True
        # VALUATION → market + fundamental 组合（§26）。
        return kind == "VALUATION" and extract_ticker(unit) is not None

    def verify(self, unit: dict[str, Any]) -> dict[str, Any]:
        return make_result(
            "NOT_FOUND",
            source_type="FUNDAMENTAL",
            provider="fundamental",
            source_id=extract_ticker(unit),
            reason="DATA_SOURCE_NOT_CONNECTED",
        )
