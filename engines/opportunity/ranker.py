"""机会排序：按 opportunity_score 降序，平分按 symbol 字典序确定性 tie-break。"""
from __future__ import annotations

from engines.opportunity.candidate import OpportunityCandidate, RankedOpportunity
from engines.opportunity.evidence import build_evidence_refs


def rank_opportunities(scored: list[tuple[OpportunityCandidate, dict]]) -> list[dict]:
    """输入 [(candidate, score_result)]，输出 RankedOpportunity dict 列表（§11.2 形状）。

    排序键 (-opportunity_score, symbol)，rank 从 1 开始连续编号。
    """
    ordered = sorted(scored, key=lambda item: (-item[1]["opportunity_score"], item[0].symbol))
    ranked: list[dict] = []
    for index, (candidate, score_result) in enumerate(ordered, start=1):
        present = [name for name, note_present in _presence(candidate).items() if note_present]
        ranked.append(
            RankedOpportunity(
                rank=index,
                symbol=candidate.symbol,
                opportunity_score=score_result["opportunity_score"],
                confidence=candidate.confidence,
                components=score_result["components"],
                evidence_refs=build_evidence_refs(candidate, present),
                trigger_conditions=list(candidate.trigger_conditions),
                invalidation_conditions=list(candidate.invalidation_conditions),
            ).model_dump()
        )
    return ranked


def _presence(candidate: OpportunityCandidate) -> dict[str, bool]:
    return {
        "theme": candidate.theme_score is not None,
        "technical": candidate.technical_score is not None,
        "alpha": candidate.factor_score is not None,
        "regime_fit": candidate.regime_fit_score is not None,
        "knowledge": candidate.knowledge_score is not None,
        "risk": candidate.risk_score is not None,
    }
