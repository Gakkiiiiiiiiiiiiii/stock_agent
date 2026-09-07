"""Low-cardinality metrics for content-only conclusion flows."""
from __future__ import annotations

import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock


class MetricPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class KnowledgeConclusionMetricEvent:
    name: str
    value: float
    labels: dict[str, str]


class InMemoryKnowledgeConclusionMetrics:
    """Thread-safe bounded metric series with a deliberately closed label policy."""

    names = frozenset({
        "stock_agent_knowledge_conclusion_total",
        "stock_agent_knowledge_conclusion_latency_seconds",
        "stock_agent_content_bundle_latency_seconds",
        "stock_agent_content_contract_failure_total",
        "stock_agent_conclusion_grounding_reject_total",
        "stock_agent_conclusion_fallback_total",
        "stock_agent_conclusion_insufficient_evidence_total",
    })
    label_keys = frozenset({"verdict", "model_mode", "error_code", "profile", "contract_version"})
    _value = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
    _forbidden = re.compile(r"(?:query|video|conclusion|url|secret|token|cookie|header|text|prompt|evidence)", re.IGNORECASE)

    def __init__(self, *, max_series: int = 128) -> None:
        self._max_series = max(1, max_series)
        self._values: OrderedDict[tuple[str, tuple[tuple[str, str], ...]], float] = OrderedDict()
        self._lock = RLock()

    def increment(self, name: str, amount: float = 1.0, **labels: str) -> None:
        self.observe(name, amount, **labels)

    def observe(self, name: str, value: float, **labels: str) -> None:
        self._validate(name, value, labels)
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + float(value)
            self._values.move_to_end(key)
            while len(self._values) > self._max_series:
                self._values.popitem(last=False)

    def snapshot(self) -> tuple[KnowledgeConclusionMetricEvent, ...]:
        with self._lock:
            return tuple(KnowledgeConclusionMetricEvent(name, value, dict(labels)) for (name, labels), value in self._values.items())

    @classmethod
    def _validate(cls, name: str, value: float, labels: dict[str, str]) -> None:
        if name not in cls.names:
            raise MetricPolicyError("metric name is not allowlisted")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise MetricPolicyError("metric value must be finite and nonnegative")
        if set(labels) - cls.label_keys:
            raise MetricPolicyError("metric label key is not allowlisted")
        for key, raw in labels.items():
            value = str(raw).strip()
            # Contract versions are fixed protocol labels, not conclusion IDs.
            is_fixed_contract = key == "contract_version" and value == "knowledge-conclusion.v1"
            if not value or not cls._value.fullmatch(value) or (cls._forbidden.search(value) and not is_fixed_contract):
                raise MetricPolicyError(f"metric label value is unsafe: {key}")
            if key == "verdict" and value not in {"SUPPORTED", "CONTRADICTED", "MIXED", "INSUFFICIENT_EVIDENCE"}:
                raise MetricPolicyError("verdict is not normalized")
            if key == "model_mode" and value not in {"MODEL", "FALLBACK"}:
                raise MetricPolicyError("model mode is not normalized")
            if key == "profile" and value not in {"knowledge-only", "full"}:
                raise MetricPolicyError("profile is not normalized")
            if key == "contract_version" and not is_fixed_contract:
                raise MetricPolicyError("contract version is not normalized")
            if key == "error_code" and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value):
                raise MetricPolicyError("error code is not normalized")
