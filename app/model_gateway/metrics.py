from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock
from time import monotonic
from typing import Any
from uuid import uuid4

_GLOBAL_STORE: "_MetricStore | None" = None


@dataclass(frozen=True)
class TraceContext:
    """Identifiers carried through model and subsystem calls."""

    trace_id: str = field(default_factory=lambda: str(uuid4()))
    decision_id: str | None = None
    bundle_id: str | None = None
    agent_run_id: str | None = None
    proposal_id: str | None = None
    snapshot_id: str | None = None

    def headers(self) -> dict[str, str]:
        values = {"X-Trace-Id": self.trace_id, "X-Caller-Service": "stock_agent"}
        for key, value in {
            "X-Decision-Id": self.decision_id,
            "X-Bundle-Id": self.bundle_id,
            "X-Agent-Run-Id": self.agent_run_id,
            "X-Proposal-Id": self.proposal_id,
            "X-Snapshot-Id": self.snapshot_id,
        }.items():
            if value is not None:
                values[key] = str(value)
        return values

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None) -> "TraceContext":
        values = values or {}
        return cls(
            trace_id=str(values.get("trace_id") or uuid4()),
            decision_id=_text(values.get("decision_id")), bundle_id=_text(values.get("bundle_id")),
            agent_run_id=_text(values.get("agent_run_id")), proposal_id=_text(values.get("proposal_id")),
            snapshot_id=_text(values.get("snapshot_id")),
        )


@dataclass(frozen=True)
class MetricEvent:
    name: str
    value: float = 1.0
    labels: dict[str, str] = field(default_factory=dict)


class _MetricStore:
    def __init__(self, max_series: int = 2048) -> None:
        self.values: OrderedDict[tuple[str, tuple[tuple[str, str], ...]], float] = OrderedDict()
        self.max_series = max(1, max_series)
        self.lock = RLock()


class MetricsRecorder:
    """Dependency-free metrics sink; production adapters can drain ``events``."""

    def __init__(self, events: list[MetricEvent] | None = None, *, max_series: int = 2048, _store: _MetricStore | None = None) -> None:
        global _GLOBAL_STORE
        if _store is not None:
            self._store = _store
        elif events is not None or max_series != 2048:
            self._store = _MetricStore(max_series=max_series)
            for event in events:
                self.observe(event.name, event.value, **event.labels)
        else:
            if _GLOBAL_STORE is None:
                _GLOBAL_STORE = _MetricStore(max_series=max_series)
            self._store = _GLOBAL_STORE

    @property
    def events(self) -> list[MetricEvent]:
        return self.snapshot()

    known_names = frozenset({
        "decision_latency_seconds", "bundle_build_latency_seconds", "specialist_latency_seconds",
        "model_latency_seconds", "policy_latency_seconds", "tool_calls_total", "tool_errors_total",
        "dependency_degraded_total", "decision_quality_total", "risk_veto_total", "policy_adjustment_total",
        "model_tokens_input", "model_tokens_output", "model_cost", "replay_total", "eval_cases_total",
    })

    def observe(self, name: str, value: float, **labels: Any) -> None:
        normalized = {key: str(value) for key, value in labels.items() if value is not None}
        key = (name, tuple(sorted(normalized.items())))
        with self._store.lock:
            self._store.values[key] = self._store.values.get(key, 0.0) + float(value)
            self._store.values.move_to_end(key)
            while len(self._store.values) > self._store.max_series:
                self._store.values.popitem(last=False)

    def increment(self, name: str, amount: float = 1.0, **labels: Any) -> None:
        self.observe(name, amount, **labels)

    def timer(self, name: str, **labels: Any):
        recorder = self
        started = monotonic()

        class _Timer:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                recorder.observe(name, monotonic() - started, **labels)
                return False

        return _Timer()

    def snapshot(self) -> list[MetricEvent]:
        with self._store.lock:
            return [MetricEvent(name=name, value=value, labels=dict(labels)) for (name, labels), value in self._store.values.items()]

    def clear(self) -> None:
        with self._store.lock:
            self._store.values.clear()


def global_metrics() -> MetricsRecorder:
    return MetricsRecorder(_store=_GLOBAL_STORE or _initialize_global_store())


def render_global_metrics() -> str:
    lines = []
    for event in global_metrics().snapshot():
        labels = ""
        if event.labels:
            labels = "{" + ",".join(f'{key}="{value}"' for key, value in sorted(event.labels.items())) + "}"
        lines.append(f"{event.name}{labels} {event.value}")
    return "\n".join(lines)


def _initialize_global_store() -> _MetricStore:
    global _GLOBAL_STORE
    if _GLOBAL_STORE is None:
        _GLOBAL_STORE = _MetricStore()
    return _GLOBAL_STORE


def _text(value: Any) -> str | None:
    return None if value is None else str(value)
