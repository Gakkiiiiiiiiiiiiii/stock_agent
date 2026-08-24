from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from evals.runners import ComparisonReport, EvalResult


def _json(value: Any) -> Any:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


@dataclass(frozen=True)
class ReportMetadata:
    schema_version: str = "decision-eval.report.v1"
    dataset_id: str = ""
    generated_by: str = "evals"


@dataclass(frozen=True)
class EvaluationReport:
    metadata: ReportMetadata
    results: tuple[EvalResult, ...]
    aggregate: Mapping[str, float | None]
    errors: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_results(cls, results: list[EvalResult] | tuple[EvalResult, ...], *, metadata: ReportMetadata | None = None) -> "EvaluationReport":
        ordered = tuple(sorted(results, key=lambda item: (item.case_id, item.variant)))
        keys = sorted({key for result in ordered for key in result.metrics})
        aggregate = {key: (sum(result.metrics[key] for result in ordered if result.status == "OK" and result.metrics.get(key) is not None) / len([result for result in ordered if result.status == "OK" and result.metrics.get(key) is not None]) if any(result.status == "OK" and result.metrics.get(key) is not None for result in ordered) else None) for key in keys}
        errors = tuple(error for result in ordered for error in (error.as_dict() for error in result.errors))
        return cls(metadata or ReportMetadata(), ordered, aggregate, errors)

    @classmethod
    def from_comparison(cls, comparison: ComparisonReport, *, metadata: ReportMetadata | None = None) -> "EvaluationReport":
        return cls.from_results(comparison.results_a + comparison.results_b, metadata=metadata)

    def as_dict(self) -> dict[str, Any]:
        return {"metadata": _json(self.metadata.__dict__), "results": [_json(result) for result in self.results], "aggregate": dict(sorted(self.aggregate.items())), "errors": [_json(error) for error in self.errors]}

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @property
    def report_hash(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


__all__ = ["EvaluationReport", "ReportMetadata"]
