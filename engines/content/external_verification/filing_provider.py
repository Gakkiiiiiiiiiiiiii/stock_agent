from __future__ import annotations

from typing import Any

from engines.content.external_verification.base import extract_ticker, make_result

# 公告/披露类 predicate（FACT → predicate 路由）。
FILING_PREDICATES = {
    "announcement",
    "filing",
    "disclosure",
    "earnings_report",
    "dividend",
    "buyback",
}


class FilingVerificationProvider:
    """公告/披露事实验证 Provider（§26：FACT → predicate-based routing）。

    占位实现：supports() 路由逻辑完整；verify() 暂返回 NOT_FOUND，
    待接入公告数据源（交易所披露 / 巨潮）后填充。
    """

    def supports(self, unit: dict[str, Any]) -> bool:
        if str(unit.get("knowledge_kind") or "").upper() != "FACT":
            return False
        return str(unit.get("predicate_key") or "").strip().lower() in FILING_PREDICATES

    def verify(self, unit: dict[str, Any]) -> dict[str, Any]:
        return make_result(
            "NOT_FOUND",
            source_type="OFFICIAL_FILING",
            provider="filing",
            source_id=extract_ticker(unit),
            reason="DATA_SOURCE_NOT_CONNECTED",
        )
