from __future__ import annotations

import os
from typing import Any, Callable


class ExternalFactVerifier:
    """Pluggable boundary for authoritative financial-data verification.

    P0-6（§22/§23 三轴状态）：外部验证只回答 Axis 2「客观上是否为真」，
    永远不改写 Axis 1「视频证据是否支持该 Claim」（support_status）。

    No video claim is promoted without a configured authoritative provider. A
    provider receives the atomic unit and returns ``MATCH``, ``CONFLICT`` or
    ``NOT_FOUND`` plus provenance. This keeps unavailable data explicit.
    """

    ELIGIBLE_KINDS = {"FACT", "VALUATION", "FINANCIAL_METRIC", "PRICE_LEVEL", "POLICY_FACT"}

    def __init__(self, provider: Callable[[dict[str, Any]], dict[str, Any]] | Any | None = None) -> None:
        self.provider = provider
        self.enabled = os.getenv("VIDEO_EXTERNAL_FACT_VERIFICATION", "0").lower() in {"1", "true", "yes"}

    def verify_many(self, units: list[dict]) -> list[dict]:
        results = []
        for unit in units:
            item = dict(unit)
            if str(item.get("knowledge_kind") or "").upper() not in self.ELIGIBLE_KINDS:
                results.append(item)
                continue
            if not self.enabled or self.provider is None:
                results.append(item | {"external_verification_status": "NOT_RUN", "truth_status": item.get("truth_status") or "NOT_CHECKED"})
                continue
            result = self._call_provider(item) or {}
            status = str(result.get("status") or "NOT_FOUND").upper()
            attributes = (item.get("attributes") or {}) | {"external_verification": result}
            if status == "MATCH":
                # 外部 MATCH 只证明事实为真，不证明视频作者说过它（§21 反例）。
                results.append(item | {"external_verification_status": "EXTERNAL_MATCH", "truth_status": "EXTERNALLY_VERIFIED", "attributes": attributes})
            elif status == "CONFLICT":
                # 兼容：旧字段可标 NEEDS_REVIEW，但 support_status 不动。
                results.append(item | {"external_verification_status": "EXTERNAL_CONFLICT", "truth_status": "EXTERNAL_CONFLICT", "verification_status": "NEEDS_REVIEW", "attributes": attributes})
            elif status == "ERROR":
                results.append(item | {"external_verification_status": "EXTERNAL_ERROR", "truth_status": item.get("truth_status") or "NOT_CHECKED", "attributes": attributes})
            else:
                results.append(item | {"external_verification_status": "EXTERNAL_NOT_FOUND", "truth_status": "NOT_FOUND", "attributes": attributes})
        return results

    def _call_provider(self, item: dict) -> dict[str, Any] | None:
        """兼容旧 callable provider 与新 AuthoritativeVerificationProvider 对象。"""
        provider = self.provider
        if hasattr(provider, "verify"):
            return provider.verify(item)
        return provider(item)
