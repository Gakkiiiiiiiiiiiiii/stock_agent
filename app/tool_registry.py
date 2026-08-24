from __future__ import annotations

"""Decision Authority tool registry.

Only read-only external evidence, deterministic decision computation, and
stock_agent-owned decision lifecycle writes are registered here. Production
of market/content/factor facts and all order/execution operations are outside
this process.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from collections.abc import Callable
from typing import Any

from app.model_providers import AnalysisModelClient
from app.tool_policy import PermissionLevel, ToolAuditor, ToolPolicy
from app.tools.definitions import ToolDefinition
from app.tools.decision_tools import build_decision_tools
from app.tools.market_tools import build_market_tools
from app.tools.memory_tools import build_memory_tools
from app.tools.portfolio_tools import build_portfolio_tools
from app.tools.regime_tools import build_regime_tools
from clients.content_client import RemoteContentClient
from clients.factor_client import RemoteFactorClient

ToolExecutor = Callable[[dict[str, Any]], dict[str, Any]]


class ClaudeToolRegistry:
    _executor_pool = ThreadPoolExecutor(max_workers=8)

    def __init__(self, analysis_model_client: AnalysisModelClient | None = None) -> None:
        self._analysis_model_client = analysis_model_client
        self.auditor = ToolAuditor()
        content = RemoteContentClient(os.getenv("CONTENT_SERVICE_URL", "http://stock-content:8100"))
        factor = RemoteFactorClient(os.getenv("FACTOR_SERVICE_URL", "http://stock-factor:8200"))
        self._tools: dict[str, tuple[dict[str, Any], ToolExecutor]] = {}

        self._register_raw(
            "search_content_knowledge", "Search read-only knowledge evidence owned by stock_content.",
            {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
            lambda payload: content.search_video_knowledge(payload["query"], limit=int(payload.get("limit", 20))),
        )
        self._register_raw(
            "get_knowledge_unit", "Read one knowledge evidence unit from stock_content.",
            {"type": "object", "properties": {"unit_id": {"type": "string"}}, "required": ["unit_id"]},
            lambda payload: content.get_knowledge_unit(payload["unit_id"]) or {},
        )
        self._register_raw(
            "get_factor_set", "Read the factor set owned by stock_factor.",
            {"type": "object", "properties": {"limit": {"type": "integer"}}},
            lambda payload: factor.list_factors(limit=int(payload.get("limit", 20))),
        )
        self._register_raw(
            "get_factor_evidence", "Read factor evidence metadata owned by stock_factor.",
            {"type": "object", "properties": {"factor_id": {"type": "string"}}, "required": ["factor_id"]},
            lambda payload: factor.get_factor_evidence(payload["factor_id"]),
        )
        for definition in (
            *build_market_tools(), *build_regime_tools(), *build_memory_tools(),
            *build_portfolio_tools(), *build_decision_tools(),
        ):
            self.register(definition)
        self._policies = self._default_policies()

    def _register_raw(self, name: str, description: str, input_schema: dict[str, Any], executor: ToolExecutor) -> None:
        self._tools[name] = ({"name": name, "description": description, "input_schema": input_schema}, executor)

    @property
    def analysis_model_client(self) -> AnalysisModelClient:
        if self._analysis_model_client is None:
            self._analysis_model_client = AnalysisModelClient()
        return self._analysis_model_client

    def register(self, definition: ToolDefinition) -> None:
        self._tools[definition.name] = (definition.anthropic_schema(), definition.executor)

    def register_many(self, definitions: list[ToolDefinition]) -> None:
        for definition in definitions:
            self.register(definition)

    def anthropic_tools(self) -> list[dict[str, Any]]:
        return [item[0] for item in self._tools.values()]

    def openai_tools(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": d["name"], "description": d["description"], "parameters": d["input_schema"]}} for d, _ in self._tools.values()]

    def describe_tool(self, name: str) -> str:
        return self._tools.get(name, ({"description": name}, None))[0].get("description", name)

    def execute(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name not in self._tools:
            return {"error": f"unknown tool: {name}"}
        payload = dict(payload or {})
        policy = self._policies.get(name, ToolPolicy(PermissionLevel.READ))
        started = time.perf_counter()
        try:
            future = self._executor_pool.submit(self._tools[name][1], payload)
            result = future.result(timeout=policy.timeout_seconds)
            result = self._limit_output(result, policy.output_limit_bytes)
            self._audit(name, payload, policy, "succeeded", int((time.perf_counter() - started) * 1000), result)
            return result
        except TimeoutError:
            self._audit(name, payload, policy, "failed", int((time.perf_counter() - started) * 1000), {"error": "TOOL_TIMEOUT"})
            return {"error": {"code": "TOOL_TIMEOUT", "message": f"tool {name} timed out after {policy.timeout_seconds}s", "tool_name": name}}
        except Exception as exc:  # noqa: BLE001
            self._audit(name, payload, policy, "failed", int((time.perf_counter() - started) * 1000), {"error": type(exc).__name__})
            return {"error": {"code": "TOOL_EXECUTION_FAILED", "message": str(exc), "tool_name": name}}

    @staticmethod
    def _default_policies() -> dict[str, ToolPolicy]:
        # Policies are intentionally explicit; unknown tools default to READ.
        policies = {name: ToolPolicy(PermissionLevel.READ) for name in {
            "search_content_knowledge", "get_knowledge_unit", "get_factor_set", "get_factor_evidence",
            "get_market_snapshot", "get_market_features", "get_sector_strength", "get_technical_evidence",
            "get_portfolio_snapshot", "get_portfolio_risk_inputs", "get_market_regime", "get_market_regime_history",
            "search_decision_memory", "search_user_preferences",
            "get_decision", "get_decision_outcome", "get_decision_history", "get_subject_state",
        }}
        for name in {"rank_opportunities", "construct_portfolio_v2", "get_factor_scores"}:
            policies[name] = ToolPolicy(PermissionLevel.COMPUTE, timeout_seconds=120)
        return policies

    @staticmethod
    def _limit_output(result: dict[str, Any], max_bytes: int) -> dict[str, Any]:
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        if len(encoded.encode("utf-8")) <= max_bytes:
            return result
        return {"warning": "TOOL_OUTPUT_TRUNCATED", "summary": encoded[: max_bytes // 2]}

    def _audit(self, name: str, payload: dict[str, Any], policy: ToolPolicy, status: str, latency_ms: int, response: dict[str, Any]) -> None:
        self.auditor.log({"tool_name": name, "permission_level": policy.permission.value, "request_payload": payload, "response_summary": response, "status": status, "latency_ms": latency_ms, "created_at": int(time.time())})


def known_tool_names() -> set[str]:
    return set(ClaudeToolRegistry()._tools)
