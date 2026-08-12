"""Factor MCP adapter.

This module deliberately contains no factor-engine imports.  It speaks the
versioned FactorClient contract and can therefore switch from the local legacy
adapter to stock_factor without changing the Tool Registry.
"""
from __future__ import annotations

from clients.factor_client import FactorClient
from contracts.factor import AlphaScoreRequest, MiningJobRequest
from services.subsystems import get_factor_client

# Compatibility exports for existing in-process callers/tests.  They are copied
# into the isolated legacy adapter immediately before a local call.
load_factor_panel = None
load_library = None


def _legacy():
    from mcp_servers import legacy_factor_mining_server

    if load_factor_panel is not None:
        legacy_factor_mining_server.load_factor_panel = load_factor_panel
    if load_library is not None:
        legacy_factor_mining_server.load_library = load_library
    return legacy_factor_mining_server


def _client() -> FactorClient:
    return get_factor_client()


def mine_factors(rounds=None, candidates_per_round=None, universe=None, days=None, eval_window=None, lease_guard=None) -> dict:
    # lease_guard belongs to the legacy in-process worker and intentionally is
    # not transported across the HTTP contract.
    from services.subsystems import factor_backend
    if factor_backend() == "local":
        return _legacy().mine_factors(
            rounds=rounds,
            candidates_per_round=candidates_per_round,
            universe=universe,
            days=days,
            eval_window=eval_window,
            lease_guard=lease_guard,
        )
    return _client().create_mining_job(MiningJobRequest(rounds=rounds, candidates_per_round=candidates_per_round, symbols=universe or [], days=days, eval_window=eval_window))


def list_factor_library(limit: int = 20) -> dict:
    from services.subsystems import factor_backend
    if factor_backend() == "local":
        return _legacy().list_factor_library(limit=limit)
    return _client().list_factors(limit=limit)


def list_recent_alpha_candidates(limit: int = 20) -> dict:
    # The public Factor v1 API only promises active factors; the local adapter
    # preserves this legacy extension until the remote recent-alpha endpoint is added.
    from services.subsystems import factor_backend
    if factor_backend() == "local":
        return _legacy().list_recent_alpha_candidates(limit=limit)
    return {"count": 0, "factors": [], "warning": "recent-alpha endpoint is not enabled by factor.v1"}


def evaluate_factor(factor_id=None, rpn=None, universe=None) -> dict:
    from services.subsystems import factor_backend
    if factor_backend() == "local":
        return _legacy().evaluate_factor(factor_id=factor_id, rpn=rpn, universe=universe)
    return _client().evaluate({"factor_id": factor_id, "rpn": rpn or [], "universe": universe or []})


def scan_alpha_factors(symbols=None) -> dict:
    from services.subsystems import factor_backend
    if factor_backend() == "local":
        return _legacy().scan_alpha_factors(symbols=symbols)
    return _client().score_alpha(AlphaScoreRequest(symbols=symbols or []))
