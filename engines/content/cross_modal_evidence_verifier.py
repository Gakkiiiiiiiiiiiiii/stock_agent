"""Cross-Modal Evidence Verifier（P0-15 / §46-47）。

对每条 claim 做 ASR × OCR 双证据交叉印证，产出真实的
CROSS_MODAL_SUPPORTED / CROSS_MODAL_CONFLICT，结果写入
unit.attributes["cross_modal_verification"]（repository 按 CROSS_MODAL 入账）。

规则（§47 OCR Numeric Gate）：
- OCR block score >= 0.95 -> HIGH；0.85~0.95 -> MEDIUM；< 0.85 不足以作 strong support。
- ASR 已 SOURCE_SUPPORTED 且 OCR HIGH/MEDIUM 且数字 + 主体双匹配
  -> 升级 CROSS_MODAL_SUPPORTED。
- ASR 数字与 OCR 数字双高置信冲突 -> NEEDS_REVIEW + reason CROSS_MODAL_CONFLICT。
- 无 OCR 证据 -> 不动。
"""

from __future__ import annotations

import re
from typing import Any

from engines.content.knowledge_enums import SupportStatus

OCR_SOURCE_TYPES = {"OCR", "VISION", "FRAME"}
HIGH_THRESHOLD = 0.95
MEDIUM_THRESHOLD = 0.85


class CrossModalEvidenceVerifier:
    def __init__(self, high_threshold: float = HIGH_THRESHOLD, medium_threshold: float = MEDIUM_THRESHOLD) -> None:
        self.high_threshold = high_threshold
        self.medium_threshold = medium_threshold

    def verify_many(self, units: list[dict], ocr_evidence: list[dict] | None = None) -> list[dict]:
        results: list[dict] = []
        for unit in units:
            results.append(self._verify_one(unit, ocr_evidence or []))
        return results

    def _verify_one(self, unit: dict, extra_ocr: list[dict]) -> dict:
        item = dict(unit)
        evidence = list(item.get("evidence") or [])
        primary = next((e for e in evidence if e.get("is_primary")), evidence[0] if evidence else None)
        ocr_items = self._overlapping_ocr(primary, evidence) + self._overlapping_ocr(primary, extra_ocr)
        if not ocr_items:
            return item

        statement = str(item.get("statement") or "")
        claim_numbers = self._numbers(statement)
        subject_tokens = self._subject_tokens(item)

        qualified: list[dict] = []  # (ocr_item, level, text, numbers, subject_hit)
        for ocr in ocr_items:
            level = self._ocr_level(ocr)
            text = self._ocr_text(ocr)
            if not text:
                continue
            numbers = self._numbers(text)
            subject_hit = not subject_tokens or any(token in text for token in subject_tokens)
            qualified.append({"ocr": ocr, "level": level, "text": text, "numbers": numbers, "subject_hit": subject_hit})
        if not qualified:
            return item

        asr_support = str(item.get("support_status") or "")
        asr_score = item.get("support_score")
        if asr_score is None:
            asr_score = item.get("support_probability")

        # 数字冲突门：主体命中 + OCR HIGH + 双侧都有数字但不一致 -> 双高置信冲突。
        for entry in qualified:
            if entry["level"] != "HIGH" or not entry["subject_hit"]:
                continue
            if claim_numbers and entry["numbers"] and not self._numbers_match(claim_numbers, entry["numbers"]):
                return item | {
                    "support_status": "NEEDS_REVIEW",
                    "verification_status": "NEEDS_REVIEW",
                    "attributes": (item.get("attributes") or {}) | {
                        "cross_modal_verification": {
                            "status": "CROSS_MODAL_CONFLICT",
                            "asr_support_score": asr_score,
                            "ocr_support_score": self._ocr_score(entry["ocr"]),
                            "matched_blocks": [self._block_ref(entry["ocr"])],
                            "reason_codes": ["CROSS_MODAL_CONFLICT"],
                            "claim_values": claim_numbers,
                            "ocr_values": entry["numbers"],
                        }
                    },
                }

        # 升级门：ASR SOURCE_SUPPORTED + OCR HIGH/MEDIUM + 数字/主体双匹配。
        if asr_support != SupportStatus.SOURCE_SUPPORTED.value:
            return item
        for entry in qualified:
            if entry["level"] not in {"HIGH", "MEDIUM"} or not entry["subject_hit"]:
                continue
            if claim_numbers and not entry["numbers"]:
                continue
            if claim_numbers and not self._numbers_match(claim_numbers, entry["numbers"]):
                continue
            return item | {
                "support_status": SupportStatus.CROSS_MODAL_SUPPORTED.value,
                "verification_status": SupportStatus.CROSS_MODAL_SUPPORTED.value,
                "attributes": (item.get("attributes") or {}) | {
                    "cross_modal_verification": {
                        "status": SupportStatus.CROSS_MODAL_SUPPORTED.value,
                        "asr_support_score": asr_score,
                        "ocr_support_score": self._ocr_score(entry["ocr"]),
                        "matched_blocks": [self._block_ref(entry["ocr"])],
                    }
                },
            }
        return item

    def _overlapping_ocr(self, primary: dict | None, candidates: list[dict]) -> list[dict]:
        items: list[dict] = []
        for candidate in candidates:
            source_type = str(candidate.get("source_type") or "OCR").upper()
            if source_type not in OCR_SOURCE_TYPES:
                continue
            if primary is None or not self._overlaps(primary, candidate):
                continue
            items.append(candidate)
        return items

    @staticmethod
    def _overlaps(left: dict, right: dict) -> bool:
        left_start, left_end = left.get("start_ms"), left.get("end_ms")
        right_start, right_end = right.get("start_ms"), right.get("end_ms")
        if None in (left_start, left_end, right_start, right_end):
            return True  # 无时间信息时不排除，交由后续门槛裁决
        return float(left_start) < float(right_end) and float(right_start) < float(left_end)

    def _ocr_level(self, ocr: dict) -> str:
        score = self._ocr_score(ocr)
        if score is None:
            return "LOW"
        if score >= self.high_threshold:
            return "HIGH"
        if score >= self.medium_threshold:
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _ocr_score(ocr: dict) -> float | None:
        metrics = ocr.get("ocr_metrics") or {}
        score = metrics.get("mean_confidence")
        if score is None:
            score = ocr.get("score")
        if score is None:
            score = ocr.get("confidence_score")
        try:
            return None if score is None else float(score)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _ocr_text(ocr: dict) -> str:
        return " ".join(
            str(ocr.get(key) or "") for key in ("raw_text", "normalized_text", "evidence_text", "text")
        ).strip()

    @staticmethod
    def _subject_tokens(unit: dict) -> list[str]:
        tokens = [str(unit.get(key) or "").strip() for key in ("subject_name", "subject_key")]
        tokens.extend(str(e.get("entity_name") or "").strip() for e in unit.get("entities") or [])
        return [token for token in tokens if len(token) >= 2]

    @staticmethod
    def _block_ref(ocr: dict) -> dict:
        return {
            "source_ref": ocr.get("source_ref"),
            "frame_id": ocr.get("frame_id"),
            "score": CrossModalEvidenceVerifier._ocr_score(ocr),
        }

    @staticmethod
    def _numbers(text: str) -> list[float]:
        from engines.content.external_verification.base import claim_numbers

        return claim_numbers(text)

    @staticmethod
    def _numbers_match(claim_values: list[float], ocr_values: list[float], tolerance: float = 0.02) -> bool:
        try:
            from engines.content.financial_numeric import numeric_values_match

            return bool(numeric_values_match(claim_values, ocr_values))
        except ImportError:
            pass
        except Exception:
            pass
        # fallback：每个 claim 数字都需有容差内的 OCR 数字对应。
        for value in claim_values:
            if not any(abs(value - other) <= tolerance * max(abs(value), 1.0) for other in ocr_values):
                return False
        return True
