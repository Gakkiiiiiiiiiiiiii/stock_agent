"""P0-13 / P0-14：Video Analysis Document 的摘要质量门与质量摘要（设计文档 §40-44）。

检索层有 KnowledgeAccessPolicy 质量门，展示层（最终视频摘要）也必须有等价的门，
否则会出现"检索层比展示层安全"的倒挂：UNSUPPORTED / NEEDS_REVIEW /
SOURCE_LOCATED 的知识虽然进不了 Qdrant，却仍可能进入面向用户的视频总结。

渲染语义（§41）：
- 事实类（FACT/VALUATION/FINANCIAL_METRIC/PRICE_LEVEL/POLICY_FACT）且
  truth_status == EXTERNALLY_VERIFIED 的 unit 才允许客观陈述，标记 [已验证]；
- 事实类但未外部验证的 unit 只能以"视频作者称…"归因形式出现，标记 [作者观点]；
- 观点/预测/风险类本质上就是作者言论，不加事实性标记。
"""

from __future__ import annotations

from engines.content.knowledge_enums import (
    HIGH_RISK_KINDS,
    KnowledgeKind,
    ReviewStatus,
    SupportStatus,
    TruthStatus,
    support_rank,
)

# §41 渲染标记：outline 行前缀，prompt 中向 LLM 说明其书写约束。
MARK_VERIFIED_FACT = "[已验证]"
MARK_AUTHOR_CLAIM = "[作者观点]"

# 摘要输入的最低支持等级：SOURCE_SUPPORTED（§41）。
_MIN_SUPPORT_RANK = support_rank(SupportStatus.SOURCE_SUPPORTED.value)

# 观点类 knowledge_kind（§42 attributed_opinions 组）。
_OPINION_KINDS = {
    KnowledgeKind.STATE.value,
    KnowledgeKind.METHOD.value,
    KnowledgeKind.CONCEPT.value,
    KnowledgeKind.CAUSAL_THESIS.value,
}

_MEASURED_QUALITY = {"HIGH", "MEDIUM", "LOW"}

# 正常化器在低质量证据时写出的 legacy support 值（knowledge_unit_normalizer）。
_LEGACY_NEEDS_REVIEW = "NEEDS_REVIEW"


def _kind(unit: dict) -> str:
    return str(unit.get("knowledge_kind") or "").strip().upper()


def _support(unit: dict) -> str:
    return str(unit.get("support_status") or unit.get("verification_status") or "").strip().upper()


def _truth(unit: dict) -> str:
    return str(unit.get("truth_status") or "").strip().upper()


def _quality(unit: dict) -> str:
    return str(unit.get("evidence_quality_status") or "").strip().upper() or "UNKNOWN"


class AnalysisDocumentPolicy:
    """摘要输入门禁 + 输入分类 + 质量摘要指标。无状态，全部为纯函数。"""

    @staticmethod
    def passes_gate(unit: dict) -> bool:
        """P0-13（§41）：低质量或被人工驳回的 unit 不进入摘要输入。"""
        if str(unit.get("review_status") or "").strip().upper() == ReviewStatus.REJECTED.value:
            return False
        return support_rank(_support(unit)) >= _MIN_SUPPORT_RANK

    @staticmethod
    def render_mark(unit: dict) -> str:
        """§41：事实类 unit 的外部验证状态决定摘要允许的书写形式。"""
        if _kind(unit) not in HIGH_RISK_KINDS:
            return ""
        if _truth(unit) == TruthStatus.EXTERNALLY_VERIFIED.value:
            return MARK_VERIFIED_FACT
        return MARK_AUTHOR_CLAIM

    @classmethod
    def classify(cls, units: list[dict]) -> dict:
        """§42：通过门禁的 unit 按摘要用途分类；被排除的只计数、不进入输入。"""
        passed = [unit for unit in units if cls.passes_gate(unit)]
        groups: dict[str, list[dict]] = {
            "verified_facts": [],
            "attributed_opinions": [],
            "forecasts": [],
            "risks": [],
            "others": [],
        }
        for unit in passed:
            kind = _kind(unit)
            if kind in HIGH_RISK_KINDS and _truth(unit) == TruthStatus.EXTERNALLY_VERIFIED.value:
                groups["verified_facts"].append(unit)
            elif kind in _OPINION_KINDS:
                groups["attributed_opinions"].append(unit)
            elif kind == KnowledgeKind.FORECAST.value:
                groups["forecasts"].append(unit)
            elif kind == KnowledgeKind.RISK_CONDITION.value:
                groups["risks"].append(unit)
            else:
                groups["others"].append(unit)
        return {
            **groups,
            "units": passed,
            "excluded_low_quality_count": len(units) - len(passed),
        }

    @classmethod
    def quality_summary(cls, units: list[dict], classified: dict | None = None) -> dict:
        """P0-14（§44）：质量构成指标，替代旧的假精确 confidence 单值。

        统计口径为进入摘要生成前的全部 unit（含被门禁排除的），
        这样才能反映"有多少知识因质量不足没有进入摘要"。
        ratio 在无分母时为 None，不伪装成 0 或 1。
        """
        classified = classified or cls.classify(units)
        total = len(units)

        def _ratio(count: int) -> float | None:
            return round(count / total, 4) if total else None

        measured = sum(1 for unit in units if _quality(unit) in _MEASURED_QUALITY)
        fact_units = [unit for unit in units if _kind(unit) in HIGH_RISK_KINDS]
        verified_fact_count = sum(1 for unit in fact_units if _truth(unit) == TruthStatus.EXTERNALLY_VERIFIED.value)
        return {
            "evidence_coverage": _ratio(sum(1 for unit in units if unit.get("evidence"))),
            "measured_evidence_ratio": _ratio(measured),
            "source_supported_ratio": _ratio(sum(1 for unit in units if support_rank(_support(unit)) >= _MIN_SUPPORT_RANK)),
            "cross_modal_supported_ratio": _ratio(
                sum(1 for unit in units if _support(unit) == SupportStatus.CROSS_MODAL_SUPPORTED.value)
            ),
            "externally_verified_fact_ratio": (
                round(verified_fact_count / len(fact_units), 4) if fact_units else None
            ),
            "needs_review_count": sum(1 for unit in units if _support(unit) == _LEGACY_NEEDS_REVIEW),
            "unsupported_count": sum(1 for unit in units if _support(unit) == SupportStatus.UNSUPPORTED.value),
            "unknown_evidence_quality_count": total - measured,
            "summary_source_unit_count": len(classified["units"]),
            "excluded_low_quality_count": classified["excluded_low_quality_count"],
        }
