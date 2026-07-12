from __future__ import annotations


def score_explanation(evidence_count: int, risk_count: int, has_invalidation: bool) -> float:
    score = evidence_count * 0.2 + risk_count * 0.1 + (0.4 if has_invalidation else 0)
    return round(max(0.0, min(1.0, score)), 4)

