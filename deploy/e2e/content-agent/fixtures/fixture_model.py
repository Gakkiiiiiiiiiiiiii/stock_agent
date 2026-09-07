"""Deterministic, local-only structured model for the isolated fixture stack.

The fixture is intentionally a projection of frozen Bundle data, never a
general model. It gives the Agent's fixed-route adapter one useful,
contract-shaped response without turning untrusted prompt content into
instructions or inventing a citation.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

_MAX_REQUEST_BYTES = 64 * 1024
_MAX_FINDINGS = 8
_MAX_TEXT = 2_000
_ACTION_TERMS = ("BUY", "SELL", "买入", "卖出", "加仓", "减仓", "ORDERCOMMAND", "EXECUTIONAUTHORIZATION", "BROKER")
_INJECTION_TERMS = ("ignore", "system", "instruction", "tool", "call", "command", "忽略", "指令", "工具", "调用", "执行")
_POSITIVE = ("增长", "上升", "改善", "提高", "扩大", "increase", "growth", "rise", "improve")
_NEGATIVE = ("下降", "下滑", "减少", "收缩", "恶化", "decrease", "decline", "fall", "deteriorat")


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _unsafe(text: str) -> bool:
    upper = text.upper()
    for term in _ACTION_TERMS[:2] + _ACTION_TERMS[6:]:
        if re.search(r"(?<![A-Z0-9])" + re.escape(term) + r"(?![A-Z0-9])", upper):
            return True
    return (
        any(term in text for term in _ACTION_TERMS[2:6])
        or any(term in text.casefold() for term in _INJECTION_TERMS[:6])
        or any(term in text for term in _INJECTION_TERMS[6:])
    )


def _polarity(text: str) -> int:
    lowered = text.casefold()
    positive = sum(term in lowered for term in _POSITIVE)
    negative = sum(term in lowered for term in _NEGATIVE)
    return (positive > negative) - (negative > positive)


def _request_bundle(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    messages = payload.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return {}
    for message in messages:
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        try:
            user = json.loads(_string(message.get("content")))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if isinstance(user, Mapping) and user.get("contract") == "knowledge-conclusion.prompt.v1":
            bundle = user.get("evidence_bundle")
            return bundle if isinstance(bundle, Mapping) else {}
    return {}


def _findings(bundle: Mapping[str, Any]) -> list[dict[str, object]]:
    items = bundle.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        items = bundle.get("knowledge")  # historical local synthesis fixture
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        return []
    found: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        knowledge_id = _string(item.get("knowledge_id") or item.get("id"))
        statement = _string(item.get("statement") or item.get("text"))
        evidence = item.get("evidence")
        if not knowledge_id or not statement or _unsafe(statement) or len(statement) > _MAX_TEXT:
            continue
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
            continue
        evidence_ids = tuple(sorted({
            _string(row.get("evidence_id") or row.get("id"))
            for row in evidence
            if isinstance(row, Mapping)
            and _string(row.get("evidence_id") or row.get("id"))
            and _string(row.get("quote") or row.get("text"))
            and _string(row.get("quote_hash"))
        }))
        if not evidence_ids:
            continue
        confidence = item.get("confidence")
        confidence = float(confidence) if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and 0 <= float(confidence) <= 1 else 0.5
        found.append({"text": statement, "knowledge_ids": [knowledge_id], "evidence_ids": list(evidence_ids), "confidence": confidence})
    return sorted(found, key=lambda row: (str(row["knowledge_ids"][0]), str(row["evidence_ids"][0]), str(row["text"])))[:_MAX_FINDINGS]


def structured_response(payload: Mapping[str, Any]) -> dict[str, object]:
    """Return only ModelConclusionPayload fields derived from frozen Bundle rows."""
    findings = _findings(_request_bundle(payload))
    if not findings:
        # Empty/malformed requests remain invalid to Agent validation, which
        # takes its audited deterministic fallback instead of guessing IDs.
        return {"verdict": "INSUFFICIENT_EVIDENCE", "market_stance": "UNCERTAIN", "findings": []}
    positive = any(_polarity(str(row["text"])) > 0 for row in findings)
    negative = any(_polarity(str(row["text"])) < 0 for row in findings)
    verdict = "MIXED" if positive and negative else "SUPPORTED" if positive else "CONTRADICTED" if negative else "INSUFFICIENT_EVIDENCE"
    return {
        "verdict": verdict,
        "market_stance": "UNCERTAIN",
        "findings": findings,
        "summary_index": 0,
        "condition_indices": [],
        "risk_indices": [],
        "contradiction_indices": [],
        "limitation_indices": list(range(len(findings))) if verdict == "INSUFFICIENT_EVIDENCE" else [],
    }


class FixtureModelHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        self._json({"status": "ok", "model": "fixture-structured-model", "contract": "knowledge-conclusion.fixture.v1"})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdigit() or int(length) > _MAX_REQUEST_BYTES or not self.headers.get("Authorization", "").startswith("Bearer "):
            self.send_error(400)
            return
        try:
            request = json.loads(self.rfile.read(int(length)))
        except (TypeError, ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        if not isinstance(request, Mapping):
            self.send_error(400)
            return
        # Never reflect a prompt: only whitelisted frozen Bundle fields reach
        # the response; unsafe/injected source text yields no findings.
        response = {
            "id": "fixture-structured-response",
            "model": "fixture-structured-model",
            "choices": [{
                "message": {"content": json.dumps(structured_response(request), ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
                "finish_reason": "stop",
            }],
        }
        self._json(response)

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, body: Mapping[str, object]) -> None:
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9000), FixtureModelHandler).serve_forever()
