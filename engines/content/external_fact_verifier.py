from __future__ import annotations

import os
from typing import Any, Callable


class ExternalFactVerifier:
    """Pluggable boundary for authoritative financial-data verification.

    No video claim is promoted without a configured authoritative provider. A
    provider receives the atomic unit and returns ``MATCH``, ``CONFLICT`` or
    ``NOT_FOUND`` plus provenance. This keeps unavailable data explicit.
    """

    ELIGIBLE_KINDS = {"FACT", "VALUATION", "FINANCIAL_METRIC"}

    def __init__(self, provider: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> None:
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
                results.append(item | {"external_verification_status": "NOT_RUN", "truth_status": item.get("truth_status") or "NOT_EXTERNALLY_VERIFIED"})
                continue
            result = self.provider(item) or {}
            status = str(result.get("status") or "NOT_FOUND").upper()
            attributes = (item.get("attributes") or {}) | {"external_verification": result}
            if status == "MATCH":
                results.append(item | {"external_verification_status": "EXTERNAL_MATCH", "truth_status": "EXTERNALLY_VERIFIED", "support_status": "EXTERNALLY_VERIFIED", "verification_status": "EXTERNALLY_VERIFIED", "attributes": attributes})
            elif status == "CONFLICT":
                results.append(item | {"external_verification_status": "EXTERNAL_CONFLICT", "truth_status": "EXTERNAL_CONFLICT", "support_status": "CONTRADICTED", "verification_status": "NEEDS_REVIEW", "attributes": attributes})
            else:
                results.append(item | {"external_verification_status": "EXTERNAL_NOT_FOUND", "truth_status": "NOT_EXTERNALLY_VERIFIED", "attributes": attributes})
        return results
