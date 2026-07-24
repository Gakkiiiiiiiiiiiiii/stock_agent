from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from app.model_providers import AnalysisModelClient
from app.tool_policy import PermissionLevel, ProposalStore, ToolAuditor, ToolPolicy, ToolPolicyError
from mcp_servers import (
    content_server,
    factor_mining_server,
    industry_knowledge_server,
    knowledge_server,
    market_data_server,
    market_regime_server,
    portfolio_server,
    portfolio_risk_server,
    retrieval_server,
    strategy_router_server,
    technical_factor_server,
    validation_server,
)


ToolExecutor = Callable[[dict[str, Any]], dict[str, Any]]


class ClaudeToolRegistry:
    def __init__(self, analysis_model_client: AnalysisModelClient | None = None) -> None:
        self.analysis_model_client = analysis_model_client or AnalysisModelClient()
        self.proposals = ProposalStore()
        self.auditor = ToolAuditor()
        self._tools: dict[str, tuple[dict[str, Any], ToolExecutor]] = {
            "get_kline": (
                {
                    "name": "get_kline",
                    "description": "Get historical K-line data for a symbol.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "start_date": {"type": "string"},
                            "end_date": {"type": "string"},
                            "freq": {"type": "string"},
                            "adjust": {"type": "string"},
                        },
                        "required": ["symbol"],
                    },
                },
                lambda payload: market_data_server.get_kline(**payload),
            ),
            "get_market_snapshot": (
                {
                    "name": "get_market_snapshot",
                    "description": "Get a structured market snapshot for the current market regime.",
                    "input_schema": {"type": "object", "properties": {}},
                },
                lambda payload: market_data_server.get_market_snapshot(),
            ),
            "get_sector_strength": (
                {
                    "name": "get_sector_strength",
                    "description": "Get sector or theme strength ranking.",
                    "input_schema": {"type": "object", "properties": {}},
                },
                lambda payload: market_data_server.get_sector_strength(),
            ),
            "calc_technical_indicators": (
                {
                    "name": "calc_technical_indicators",
                    "description": "Calculate technical indicators for a symbol.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "end_date": {"type": "string"},
                        },
                        "required": ["symbol"],
                    },
                },
                lambda payload: technical_factor_server.calc_technical_indicators(**payload),
            ),
            "calc_profile_indicators": (
                {
                    "name": "calc_profile_indicators",
                    "description": "Calculate versioned technical profile indicators with profile hash and data metadata.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "profile": {"type": "string"},
                            "end_date": {"type": "string"},
                        },
                        "required": ["symbol"],
                    },
                },
                lambda payload: technical_factor_server.calc_profile_indicators(**payload),
            ),
            "evaluate_technical_rules": (
                {
                    "name": "evaluate_technical_rules",
                    "description": "Evaluate approved technical rule-pack DSL with three-valued logic and node evidence.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "rule_pack": {"type": "string"},
                            "end_date": {"type": "string"},
                        },
                        "required": ["symbol"],
                    },
                },
                lambda payload: technical_factor_server.evaluate_technical_rules(**payload),
            ),
            "detect_pattern_signal": (
                {
                    "name": "detect_pattern_signal",
                    "description": "Detect B1/B2/B3/MACD_TRIPLE_GOLDEN pattern signals for a symbol.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "date": {"type": "string"},
                            "patterns": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["symbol"],
                    },
                },
                lambda payload: technical_factor_server.detect_pattern_signal(**payload),
            ),
            "scan_stock_signals": (
                {
                    "name": "scan_stock_signals",
                    "description": "Batch scan symbols for technical pattern signals.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "symbols": {"type": "array", "items": {"type": "string"}},
                            "patterns": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["symbols"],
                    },
                },
                lambda payload: technical_factor_server.scan_stock_signals(**payload),
            ),
            "search_theme_logic": (
                {
                    "name": "search_theme_logic",
                    "description": "Search a theme logic from the knowledge base.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "theme_name": {"type": "string"},
                            "include_stocks": {"type": "boolean"},
                            "include_trigger_rules": {"type": "boolean"},
                        },
                        "required": ["theme_name"],
                    },
                },
                lambda payload: knowledge_server.search_theme_logic(**payload),
            ),
            "retrieve_relevant_context": (
                {
                    "name": "retrieve_relevant_context",
                    "description": "Retrieve relevant memory and knowledge contexts using Qdrant, reranking, and PostgreSQL hydration.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "task_type": {"type": "string"},
                            "filters": {"type": "object"},
                            "top_k": {"type": "integer"},
                        },
                        "required": ["query"],
                    },
                },
                lambda payload: retrieval_server.retrieve_relevant_context(**payload),
            ),
            "get_theme_related_stocks": (
                {
                    "name": "get_theme_related_stocks",
                    "description": "Get related stocks for a theme.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"theme_name": {"type": "string"}},
                        "required": ["theme_name"],
                    },
                },
                lambda payload: industry_knowledge_server.get_theme_related_stocks_tool(**payload),
            ),
            "upsert_theme_logic": (
                {
                    "name": "upsert_theme_logic",
                    "description": "Create or update a theme logic record.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "theme_name": {"type": "string"},
                            "core_thesis": {"type": "string"},
                            "industry_chain": {"type": "array", "items": {"type": "string"}},
                            "catalysts": {"type": "array", "items": {"type": "string"}},
                            "monitor_keywords": {"type": "array", "items": {"type": "string"}},
                            "trigger_rules": {"type": "array", "items": {"type": "string"}},
                            "invalidation_rules": {"type": "array", "items": {"type": "string"}},
                            "risks": {"type": "array", "items": {"type": "string"}},
                            "related_stocks": {"type": "array", "items": {"type": "object"}},
                        },
                        "required": ["theme_name"],
                    },
                },
                lambda payload: knowledge_server.upsert_theme_logic(payload),
            ),
            "evaluate_theme_trigger": (
                {
                    "name": "evaluate_theme_trigger",
                    "description": "Check whether an event matches a known investment theme.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "theme_name": {"type": "string"},
                            "event_title": {"type": "string"},
                            "event_content": {"type": "string"},
                        },
                        "required": ["theme_name", "event_title"],
                    },
                },
                lambda payload: industry_knowledge_server.evaluate_theme_trigger(**payload),
            ),
            "rank_themes_by_score": (
                {
                    "name": "rank_themes_by_score",
                    "description": "Rank themes by weighted score input.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"items": {"type": "array", "items": {"type": "object"}}},
                        "required": ["items"],
                    },
                },
                lambda payload: industry_knowledge_server.rank_themes_by_score(**payload),
            ),
            "get_market_regime": (
                {
                    "name": "get_market_regime",
                    "description": "Infer current market regime, state transitions, and high-position retreat risk.",
                    "input_schema": {"type": "object", "properties": {}},
                },
                lambda payload: market_regime_server.get_market_regime(**payload),
            ),
            "route_strategy": (
                {
                    "name": "route_strategy",
                    "description": "Route preferred strategies and risk limits by market regime.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"market_regime": {"type": "string"}},
                        "required": ["market_regime"],
                    },
                },
                lambda payload: strategy_router_server.route_strategy(**payload),
            ),
            "adjust_signal": (
                {
                    "name": "adjust_signal",
                    "description": "Adjust a raw signal according to market regime, theme strength, and liquidity.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string"},
                            "raw_signal_score": {"type": "number"},
                            "market_regime": {"type": "string"},
                            "theme_strength": {"type": "number"},
                            "liquidity_ok": {"type": "boolean"},
                        },
                        "required": ["pattern", "raw_signal_score", "market_regime"],
                    },
                },
                lambda payload: strategy_router_server.adjust_signal(**payload),
            ),
            "evaluate_portfolio_risk": (
                {
                    "name": "evaluate_portfolio_risk",
                    "description": "Evaluate single-name concentration and theme exposure risk for a portfolio.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"positions": {"type": "array", "items": {"type": "object"}}},
                        "required": ["positions"],
                    },
                },
                lambda payload: portfolio_risk_server.evaluate_portfolio_risk_tool(**payload),
            ),
            "construct_portfolio": (
                {
                    "name": "construct_portfolio",
                    "description": "Construct portfolio actions from candidates and current positions under risk limits.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "candidates": {"type": "array", "items": {"type": "object"}},
                            "positions": {"type": "array", "items": {"type": "object"}},
                            "risk_limits": {"type": "object"},
                        },
                        "required": ["candidates", "positions", "risk_limits"],
                    },
                },
                lambda payload: portfolio_server.construct_portfolio(**payload),
            ),
            "walk_forward_validate": (
                {
                    "name": "walk_forward_validate",
                    "description": "Run a lightweight validation pass over a price series.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "closes": {"type": "array", "items": {"type": "number"}},
                            "initial_cash": {"type": "number"},
                        },
                        "required": ["closes"],
                    },
                },
                lambda payload: validation_server.walk_forward_validate(**payload),
            ),
            "ask_research_model": (
                {
                    "name": "ask_research_model",
                    "description": "Ask the configured external analysis model, such as DeepSeek, for supplemental reasoning or drafting. Claude remains the orchestrator and final reporter.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string"},
                            "system": {"type": "string"},
                            "temperature": {"type": "number"},
                        },
                        "required": ["prompt"],
                    },
                },
                self._ask_research_model,
            ),
            "ingest_bilibili_video": (
                {
                    "name": "ingest_bilibili_video",
                    "description": "Create a content ingestion task for a Bilibili video and process it into transcript and investment summary.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "bv_id": {"type": "string"},
                            "force_reprocess": {"type": "boolean"},
                            "summary_mode": {"type": "string"},
                            "index_to_memory": {"type": "boolean"},
                            "use_diarization": {"type": "boolean"},
                            "language_hint": {"type": "string"},
                        },
                    },
                },
                lambda payload: content_server.ingest_bilibili_video(**payload),
            ),
            "get_video_summary": (
                {
                    "name": "get_video_summary",
                    "description": "Fetch a previously processed Bilibili video summary and transcript detail.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "video_id": {"type": "integer"},
                            "summary_mode": {"type": "string"},
                        },
                        "required": ["video_id"],
                    },
                },
                lambda payload: content_server.get_video_summary(**payload),
            ),
            "search_video_insights": (
                {
                    "name": "search_video_insights",
                    "description": "Search indexed Bilibili video insights from the knowledge memory store.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "top_k": {"type": "integer"},
                            "themes": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["query"],
                    },
                },
                lambda payload: content_server.search_video_insights(**payload),
            ),
            "mine_factors": (
                {
                    "name": "mine_factors",
                    "description": "Mine cross-sectional alpha factors automatically with the analysis LLM, evaluate them in-sample (RankIC/ICIR/TopK), and store passing factors into the factor library. Results are in-sample and marked as unverified.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "rounds": {"type": "integer"},
                            "candidates_per_round": {"type": "integer"},
                            "universe": {"type": "array", "items": {"type": "string"}},
                            "days": {"type": "integer"},
                            "eval_window": {"type": "integer"},
                        },
                    },
                },
                lambda payload: factor_mining_server.mine_factors(**payload),
            ),
            "list_factor_library": (
                {
                    "name": "list_factor_library",
                    "description": "List active mined factors with their in-sample metrics.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"limit": {"type": "integer"}},
                    },
                },
                lambda payload: factor_mining_server.list_factor_library(**payload),
            ),
            "evaluate_factor": (
                {
                    "name": "evaluate_factor",
                    "description": "Re-evaluate a mined factor (by library id or raw RPN formula) on the current universe.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "factor_id": {"type": "string"},
                            "rpn": {"type": "array", "items": {"type": "string"}},
                            "universe": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                lambda payload: factor_mining_server.evaluate_factor(**payload),
            ),
            "scan_alpha_factors": (
                {
                    "name": "scan_alpha_factors",
                    "description": "Score and rank symbols by an equal-weight composite of active mined factors (in-sample, unverified).",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "symbols": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                lambda payload: factor_mining_server.scan_alpha_factors(**payload),
            ),
        }
        self._policies: dict[str, ToolPolicy] = self._default_policies()

    def anthropic_tools(self) -> list[dict[str, Any]]:
        return [item[0] for item in self._tools.values()]

    def openai_tools(self) -> list[dict[str, Any]]:
        tools = []
        for tool_def, _ in self._tools.values():
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool_def["name"],
                        "description": tool_def["description"],
                        "parameters": tool_def["input_schema"],
                    },
                }
            )
        return tools

    def describe_tool(self, name: str) -> str:
        if name not in self._tools:
            return name
        tool_def, _ = self._tools[name]
        return tool_def.get("description", name)

    def execute(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name not in self._tools:
            return {"error": f"unknown tool: {name}"}
        payload = dict(payload or {})
        policy = self._policies.get(name, ToolPolicy(PermissionLevel.READ))
        confirmation_id = None
        if policy.permission in {PermissionLevel.CONFIRMED_WRITE, PermissionLevel.ADMIN} or policy.requires_confirmation:
            token = payload.pop("confirmation_token", None)
            try:
                confirmation_id = self.proposals.verify(name, payload, token)
            except ToolPolicyError as exc:
                self._audit(name, payload, policy, "denied", confirmation_id, 0, {"code": exc.code})
                return {"error": {"code": exc.code, "message": exc.message}}
        started = time.perf_counter()
        _, executor = self._tools[name]
        try:
            result = executor(payload)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            result = self._limit_output(result, policy.output_limit_bytes)
            self._audit(name, payload, policy, "succeeded", confirmation_id, elapsed_ms, result)
            return result
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            self._audit(name, payload, policy, "failed", confirmation_id, elapsed_ms, {"error": type(exc).__name__})
            return {"error": {"code": "TOOL_EXECUTION_FAILED", "message": str(exc), "tool_name": name}}

    def create_proposal(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if tool_name not in self._tools:
            return {"error": {"code": "UNKNOWN_TOOL", "message": f"unknown tool: {tool_name}"}}
        policy = self._policies.get(tool_name, ToolPolicy(PermissionLevel.READ))
        if policy.permission not in {PermissionLevel.CONFIRMED_WRITE, PermissionLevel.ADMIN}:
            return {"error": {"code": "PROPOSAL_NOT_REQUIRED", "message": f"tool {tool_name} does not require write confirmation"}}
        return self.proposals.create(tool_name, payload)

    def approve_proposal(self, proposal_id: str) -> dict[str, Any]:
        try:
            return self.proposals.approve(proposal_id)
        except ToolPolicyError as exc:
            return {"error": {"code": exc.code, "message": exc.message}}

    def _ask_research_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.analysis_model_client.complete(
            prompt=payload["prompt"],
            system=payload.get("system"),
            temperature=float(payload.get("temperature", 0.2)),
        )

    @staticmethod
    def _default_policies() -> dict[str, ToolPolicy]:
        compute = {
            "walk_forward_validate",
            "mine_factors",
            "evaluate_factor",
            "scan_alpha_factors",
            "construct_portfolio",
            "ingest_bilibili_video",
        }
        writes = {"upsert_theme_logic"}
        policies = {name: ToolPolicy(PermissionLevel.READ) for name in (
            "get_kline", "get_market_snapshot", "get_sector_strength", "calc_technical_indicators",
            "calc_profile_indicators", "evaluate_technical_rules",
            "detect_pattern_signal", "scan_stock_signals", "search_theme_logic", "retrieve_relevant_context",
            "get_theme_related_stocks", "evaluate_theme_trigger", "rank_themes_by_score", "get_market_regime",
            "route_strategy", "adjust_signal", "evaluate_portfolio_risk", "ask_research_model",
            "get_video_summary", "search_video_insights", "list_factor_library",
        )}
        policies.update({name: ToolPolicy(PermissionLevel.COMPUTE, timeout_seconds=120) for name in compute})
        policies.update({name: ToolPolicy(PermissionLevel.CONFIRMED_WRITE, requires_confirmation=True) for name in writes})
        return policies

    @staticmethod
    def _limit_output(result: dict[str, Any], max_bytes: int) -> dict[str, Any]:
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        if len(encoded.encode("utf-8")) <= max_bytes:
            return result
        return {
            "warning": "TOOL_OUTPUT_TRUNCATED",
            "summary": encoded[: max_bytes // 2],
        }

    def _audit(
        self,
        name: str,
        payload: dict[str, Any],
        policy: ToolPolicy,
        status: str,
        confirmation_id: str | None,
        latency_ms: int,
        response: dict[str, Any],
    ) -> None:
        self.auditor.log({
            "tool_name": name,
            "permission_level": policy.permission.value,
            "request_payload": payload,
            "response_summary": response,
            "status": status,
            "latency_ms": latency_ms,
            "confirmation_id": confirmation_id,
            "created_at": int(time.time()),
        })
