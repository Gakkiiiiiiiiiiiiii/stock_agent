from __future__ import annotations

import re
from typing import Any


class ClaimEvidenceVerifier:
    """Conservative claim-to-evidence support checker.

    This deliberately separates a located source from semantic support.  The
    rule layer is deterministic and auditable; an optional model judge can be
    introduced later without weakening these numeric/entity/negation checks.
    """

    HIGH_RISK_KINDS = {"FACT", "VALUATION", "FINANCIAL_METRIC", "PRICE_LEVEL", "POLICY_FACT"}
    NEGATIONS = ("不", "未", "没有", "并非", "无", "勿", "别")
    POSITIVE = ("上涨", "增长", "改善", "看多", "偏强", "突破", "加仓")
    NEGATIVE = ("下跌", "下降", "恶化", "看空", "偏弱", "跌破", "减仓")

    def verify(self, unit: dict[str, Any]) -> dict[str, Any]:
        evidence = list(unit.get("evidence") or [])
        primary = next((item for item in evidence if item.get("is_primary")), evidence[0] if evidence else None)
        if not primary or primary.get("start_ms") is None or primary.get("end_ms") is None:
            return self._result("UNSUPPORTED", 0.0, ["EVIDENCE_NOT_LOCATED"], {})
        source = " ".join(
            str(primary.get(key) or "")
            for key in ("raw_text", "normalized_text", "evidence_text")
        )
        claim = str(unit.get("statement") or "")
        if not source.strip() or not claim:
            return self._result("UNSUPPORTED", 0.0, ["EMPTY_CLAIM_OR_EVIDENCE"], {})
        source_norm, claim_norm = self._compact(source), self._compact(claim)
        checks = {
            "number_match": self._numbers_match(claim_norm, source_norm),
            "entity_match": self._entity_match(unit, source_norm),
            "direction_match": self._direction_match(claim_norm, source_norm),
            "negation_match": self._negation_match(claim_norm, source_norm),
            "condition_match": self._condition_match(unit, source_norm),
            "source_located": True,
        }
        reasons = [f"{key.upper()}_FAILED" for key, passed in checks.items() if not passed and key != "source_located"]
        keyword_score = self._keyword_overlap(claim_norm, source_norm)
        checks["semantic_overlap"] = keyword_score >= 0.18
        if not checks["semantic_overlap"]:
            reasons.append("SEMANTIC_OVERLAP_LOW")
        score = (sum(1.0 for key, passed in checks.items() if key != "source_located" and passed) / 5.0) * 0.75 + keyword_score * 0.25
        high_risk = str(unit.get("knowledge_kind") or "").upper() in self.HIGH_RISK_KINDS
        supported = not reasons and score >= (0.82 if high_risk else 0.65)
        return self._result("SOURCE_SUPPORTED" if supported else "NEEDS_REVIEW", round(score, 4), reasons, checks)

    @staticmethod
    def _compact(value: str) -> str:
        return re.sub(r"\s+", "", value).lower()

    @staticmethod
    def _numbers_match(claim: str, source: str) -> bool:
        numbers = re.findall(r"\d+(?:\.\d+)?%?|\d+月\d+日", claim)
        return all(number in source for number in numbers)

    @classmethod
    def _entity_match(cls, unit: dict[str, Any], source: str) -> bool:
        names = [str(unit.get(key) or "").strip() for key in ("subject_name", "subject_key")]
        names.extend(str(item.get("entity_name") or item.get("ticker") or "").strip() for item in unit.get("entities") or [])
        names = [cls._compact(name) for name in names if len(cls._compact(name)) >= 2]
        return not names or any(name in source for name in names)

    @classmethod
    def _direction_match(cls, claim: str, source: str) -> bool:
        def polarity(text: str) -> int:
            return int(any(token in text for token in cls.POSITIVE)) - int(any(token in text for token in cls.NEGATIVE))
        return polarity(claim) == 0 or polarity(claim) == polarity(source)

    @classmethod
    def _negation_match(cls, claim: str, source: str) -> bool:
        return not any(token in claim for token in cls.NEGATIONS) or any(token in source for token in cls.NEGATIONS)

    @classmethod
    def _condition_match(cls, unit: dict[str, Any], source: str) -> bool:
        condition = cls._compact(str(unit.get("condition_text") or ""))
        return not condition or condition in source

    @staticmethod
    def _keyword_overlap(claim: str, source: str) -> float:
        # Chinese character bigrams give an explainable approximation for a
        # semantic candidate check without treating LLM confidence as evidence.
        tokens = {claim[index : index + 2] for index in range(len(claim) - 1) if claim[index : index + 2].strip()}
        if not tokens:
            return 1.0
        return len({token for token in tokens if token in source}) / len(tokens)

    @staticmethod
    def _result(status: str, score: float, reasons: list[str], checks: dict[str, bool]) -> dict[str, Any]:
        return {"support_status": status, "support_probability": score, "reason_codes": reasons, "checks": checks}
