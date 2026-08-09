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

    项目当前没有公告/披露数据源（交易所披露 / 巨潮均未接入），按设计文档
    §6.3 选项二明确标记为不支持：verify() 恒返回 NOT_FOUND +
    reason=EXTERNAL_VERIFICATION_NOT_SUPPORTED。ExternalFactVerifier 因此只会把
    公告类 unit 置为 truth_status=NOT_FOUND，永远不会进入 EXTERNALLY_VERIFIED，
    factual_qa（要求 EXTERNALLY_VERIFIED）不会用视频知识回答此类事实。
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
            reason="EXTERNAL_VERIFICATION_NOT_SUPPORTED",
        )
