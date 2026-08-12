from __future__ import annotations

from contracts.factor import AlphaScoreRequest, MiningJobRequest
from services.subsystems import get_factor_client


def mine_factors(rounds=None, candidates_per_round=None, universe=None, days=None, eval_window=None, lease_guard=None) -> dict:
    return get_factor_client().create_mining_job(
        MiningJobRequest(
            rounds=rounds,
            candidates_per_round=candidates_per_round,
            symbols=universe or [],
            days=days,
            eval_window=eval_window,
        )
    )


def list_factor_library(limit: int = 20) -> dict:
    return get_factor_client().list_factors(limit=limit)


def list_recent_alpha_candidates(limit: int = 20) -> dict:
    return get_factor_client().list_factors(limit=limit)


def evaluate_factor(factor_id=None, rpn=None, universe=None) -> dict:
    return get_factor_client().evaluate({"factor_id": factor_id, "rpn": rpn or [], "symbols": universe or []})


def scan_alpha_factors(symbols=None) -> dict:
    return get_factor_client().score_alpha(AlphaScoreRequest(symbols=symbols or []))
