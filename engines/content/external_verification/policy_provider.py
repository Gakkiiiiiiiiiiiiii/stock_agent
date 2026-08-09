from __future__ import annotations

from typing import Any

from engines.content.external_verification.base import make_result


class PolicyVerificationProvider:
    """政策事实验证 Provider（§26：POLICY_FACT → official policy / government source）。

    项目当前没有官方政策/政府数据源，按设计文档 §6.4 选项二明确标记为不支持：
    verify() 恒返回 NOT_FOUND + reason=EXTERNAL_VERIFICATION_NOT_SUPPORTED。
    ExternalFactVerifier 因此只会把 POLICY_FACT unit 置为 truth_status=NOT_FOUND，
    永远不会进入 EXTERNALLY_VERIFIED，factual_qa（要求 EXTERNALLY_VERIFIED 的
    RetrievalPolicy）不会用视频知识回答政策类事实。
    """

    def supports(self, unit: dict[str, Any]) -> bool:
        return str(unit.get("knowledge_kind") or "").upper() == "POLICY_FACT"

    def verify(self, unit: dict[str, Any]) -> dict[str, Any]:
        return make_result(
            "NOT_FOUND",
            source_type="POLICY_SOURCE",
            provider="policy",
            source_id=str(unit.get("subject_key") or "") or None,
            reason="EXTERNAL_VERIFICATION_NOT_SUPPORTED",
        )
