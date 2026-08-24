from __future__ import annotations

from dataclasses import dataclass
import json
from math import isfinite
from pathlib import Path
from typing import Any, Mapping


def _validate_metrics(metrics: Mapping[str, Any]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for key, value in metrics.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("baseline metric names must be nonblank")
        if value is not None:
            value = float(value)
            if not isfinite(value):
                raise ValueError(f"baseline metric {key} must be finite")
        result[key] = value
    return dict(sorted(result.items()))


@dataclass(frozen=True)
class Baseline:
    baseline_id: str
    metrics: Mapping[str, float | None]
    schema_version: str = "decision-eval.baseline.v1"
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.baseline_id.strip():
            raise ValueError("baseline_id must be nonblank")
        object.__setattr__(self, "metrics", _validate_metrics(self.metrics))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "baseline_id": self.baseline_id, "metrics": dict(self.metrics), "metadata": self.metadata}

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class BaselineIssue:
    metric: str
    baseline: float
    current: float
    delta: float
    tolerance: float
    direction: str = "higher"

    def as_dict(self) -> dict[str, Any]:
        return {"metric": self.metric, "baseline": self.baseline, "current": self.current, "delta": self.delta, "tolerance": self.tolerance, "direction": self.direction}


LOWER_IS_BETTER = frozenset({
    "stale_evidence_rate", "unsupported_claim_rate", "contract_violation_rate",
    "false_veto_rate", "missed_veto_rate", "portfolio_constraint_violation_rate",
    "drawdown", "brier_score", "ece", "forbidden_claim_rate",
    "target_weight_constraint_violation_rate",
})


def compare_to_baseline(current: Mapping[str, Any], baseline: Baseline | Mapping[str, Any], *, tolerances: Mapping[str, float] | None = None, directions: Mapping[str, str] | None = None, default_tolerance: float = 0.0) -> tuple[BaselineIssue, ...]:
    """Return regressions only; absent/undefined metrics are not guessed.

    Direction is built in for quality/risk metrics: recall, precision and
    returns are higher-is-better; stale/error/risk-loss/calibration-loss
    metrics are lower-is-better.  ``directions`` can override a metric.
    """

    if not isinstance(baseline, Baseline):
        baseline = Baseline(baseline_id=str(baseline.get("baseline_id", "baseline")), metrics=baseline.get("metrics", baseline), schema_version=str(baseline.get("schema_version", "decision-eval.baseline.v1")), metadata=baseline.get("metadata", {}))
    tolerances = tolerances or {}
    issues: list[BaselineIssue] = []
    for metric in sorted(baseline.metrics):
        old = baseline.metrics[metric]
        new = current.get(metric)
        if old is None or new is None:
            continue
        old, new = float(old), float(new)
        tolerance = float(tolerances.get(metric, default_tolerance))
        direction = (directions or {}).get(metric, "lower" if metric in LOWER_IS_BETTER else "higher")
        if direction not in {"higher", "lower"}:
            raise ValueError(f"invalid baseline direction for {metric}: {direction}")
        regressed = new < old - tolerance if direction == "higher" else new > old + tolerance
        if regressed:
            issues.append(BaselineIssue(metric, old, new, new - old, tolerance, direction))
    return tuple(issues)


def load_baseline(path: str | Path) -> Baseline:
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"baseline must be a regular file: {source}")
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("baseline JSON must be an object")
    return Baseline(baseline_id=str(value.get("baseline_id", "")), metrics=value.get("metrics", {}), schema_version=str(value.get("schema_version", "decision-eval.baseline.v1")), metadata=value.get("metadata", {}))


__all__ = ["Baseline", "BaselineIssue", "compare_to_baseline", "load_baseline"]
