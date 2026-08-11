"""机会排序服务：eligibility filter → score → rank，输出带 meta 的结果。"""
from __future__ import annotations

from engines.opportunity.candidate import OpportunityCandidate
from engines.opportunity.eligibility import evaluate_eligibility
from engines.opportunity.ranker import rank_opportunities
from engines.opportunity.scorer import DEFAULT_VERSION, load_opportunity_config, score_candidate
from engines.versioning import get_version


class OpportunityRankingService:
    """确定性机会排序服务（无 LLM）。

    rank(candidates, context) 返回：
      {
        "ranked":   [RankedOpportunity dict, ...],   # 按 rank 升序
        "rejected": [{"symbol", "eligible": False, "reject_reasons": [...]}, ...],
        "meta":     {"as_of", "calculation_version", "candidate_count",
                     "ranked_count", "rejected_count", "weights"},
      }
    """

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or load_opportunity_config()

    def rank(self, candidates: list[dict | OpportunityCandidate], context: dict | None = None) -> dict:
        ctx = context or {}
        symbol_contexts = ctx.get("symbols") or {}
        eligibility_cfg = self.config.get("eligibility") or {}
        min_liquidity = float(eligibility_cfg.get("min_liquidity_score", 20.0))
        min_coverage = float(eligibility_cfg.get("min_data_coverage", 0.60))

        parsed = [
            item if isinstance(item, OpportunityCandidate) else OpportunityCandidate.model_validate(item)
            for item in candidates
        ]
        scored: list[tuple[OpportunityCandidate, dict]] = []
        rejected: list[dict] = []
        for candidate in parsed:
            verdict = evaluate_eligibility(
                candidate,
                symbol_contexts.get(candidate.symbol) or {},
                min_liquidity_score=min_liquidity,
                min_data_coverage=min_coverage,
            )
            if verdict["eligible"]:
                scored.append((candidate, score_candidate(candidate, self.config)))
            else:
                rejected.append(verdict)
        rejected.sort(key=lambda item: item["symbol"])
        ranked = rank_opportunities(scored)
        version = get_version("opportunity_ranking_version") or self.config.get("version") or DEFAULT_VERSION
        return {
            "ranked": ranked,
            "rejected": rejected,
            "meta": {
                "as_of": ctx.get("as_of"),
                "calculation_version": version,
                "candidate_count": len(parsed),
                "ranked_count": len(ranked),
                "rejected_count": len(rejected),
                "weights": dict(self.config.get("weights") or {}),
            },
        }
