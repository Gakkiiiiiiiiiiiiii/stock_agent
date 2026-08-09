from __future__ import annotations

from typing import Any

from engines.content.external_verification.base import make_result


class PolicyVerificationProvider:
    """政策事实验证 Provider（§26：POLICY_FACT → official policy / government source）。

    占位实现：supports() 路由逻辑完整；verify() 暂返回 NOT_FOUND，
    待接入官方政策/政府数据源后填充。
    """

    def supports(self, unit: dict[str, Any]) -> bool:
        return str(unit.get("knowledge_kind") or "").upper() == "POLICY_FACT"

    def verify(self, unit: dict[str, Any]) -> dict[str, Any]:
        return make_result(
            "NOT_FOUND",
            source_type="POLICY_SOURCE",
            provider="policy",
            source_id=str(unit.get("subject_key") or "") or None,
            reason="DATA_SOURCE_NOT_CONNECTED",
        )
