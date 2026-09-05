"""Small Prometheus-compatible low-cardinality metric registry."""
from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import ClassVar


class MetricsRegistry:
    ALLOWED: ClassVar[set[str]] = {
        "formal_decision_requests_total", "formal_decision_stage_duration_seconds",
        "formal_readiness", "upstream_artifact_age_seconds", "decision_replay_mismatch_total",
        "decision_outbox_lag_seconds", "legacy_endpoint_requests_total", "outcome_evaluation_lag_seconds",
    }
    def __init__(self):
        self._values: defaultdict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._lock = Lock()

    def inc(self, name: str, value: float = 1, **labels: str) -> None:
        if name not in self.ALLOWED:
            raise ValueError(f"UNKNOWN_METRIC:{name}")
        # identities are deliberately rejected as labels to prevent cardinality leaks.
        if any(key.endswith("_id") or key in {"decision_id", "request_id", "trace_id"} for key in labels):
            raise ValueError("HIGH_CARDINALITY_LABEL")
        key = (name, tuple(sorted((k, str(v)) for k, v in labels.items())))
        with self._lock:
            self._values[key] += value

    def render(self) -> str:
        with self._lock:
            return "\n".join(f"{name}{_labels(labels)} {value:g}" for (name, labels), value in sorted(self._values.items())) + "\n"


def _labels(labels: tuple[tuple[str, str], ...]) -> str:
    return "" if not labels else "{" + ",".join(f'{k}="{v}"' for k, v in labels) + "}"


metrics = MetricsRegistry()
