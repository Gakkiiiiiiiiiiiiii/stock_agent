from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Sequence

from evals.datasets import DecisionEvalCase

METRIC_NAMES = (
    "evidence_recall", "evidence_precision", "critical_evidence_coverage",
    "stale_evidence_rate", "unsupported_claim_rate", "tool_success_rate",
    "specialist_completion_rate", "contract_violation_rate", "decision_stability",
    "risk_recall", "false_veto_rate", "missed_veto_rate",
    "portfolio_constraint_violation_rate", "return", "excess_return",
    "drawdown", "hit_rate", "confidence_calibration", "brier_score", "ece",
    "forbidden_claim_rate", "acceptable_action_rate", "target_weight_constraint_violation_rate",
)


@dataclass(frozen=True)
class MetricValue:
    """A metric with its denominator, making empty-set semantics inspectable."""

    value: float | None
    numerator: float
    denominator: float
    defined: bool

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "numerator": self.numerator, "denominator": self.denominator, "defined": self.defined}


def _ratio(numerator: float, denominator: float, *, empty: float | None = 0.0) -> MetricValue:
    if denominator == 0:
        return MetricValue(empty, numerator, denominator, False)
    return MetricValue(numerator / denominator, numerator, denominator, True)


def _mapping(output: Any) -> Mapping[str, Any]:
    if hasattr(output, "model_dump"):
        output = output.model_dump(mode="json")
    if not isinstance(output, Mapping):
        raise TypeError("eval output must be a mapping or Pydantic model")
    return output


def _items(output: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = output.get("evidence", [])
    if isinstance(raw, Mapping):
        raw = list(raw.values())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raw = []
    items: list[Mapping[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            items.append(item)
    types = output.get("evidence_types", [])
    refs = output.get("evidence_refs", [])
    if not items and isinstance(types, Sequence) and not isinstance(types, (str, bytes)):
        ref_values = list(refs) if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes)) else []
        items = [{"evidence_type": value, "evidence_id": ref_values[index] if index < len(ref_values) else None} for index, value in enumerate(types)]
    return items


def _types(output: Mapping[str, Any]) -> list[str]:
    result = []
    for item in _items(output):
        value = item.get("evidence_type")
        if hasattr(value, "value"):
            value = value.value
        if isinstance(value, str):
            result.append(value)
    return result


def _refs(output: Mapping[str, Any]) -> set[str]:
    result = set()
    raw = output.get("evidence_refs", [])
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        result.update(str(item) for item in raw)
    for item in _items(output):
        if item.get("evidence_id") is not None:
            result.add(str(item["evidence_id"]))
    return result


def evidence_recall(case: DecisionEvalCase, output: Any) -> MetricValue:
    required = set(case.expected.required_evidence_types)
    actual = set(_types(_mapping(output)))
    return _ratio(len(required & actual), len(required), empty=1.0)


def evidence_precision(case: DecisionEvalCase, output: Any) -> MetricValue:
    actual = _types(_mapping(output))
    required = set(case.expected.required_evidence_types)
    relevant = sum(value in required for value in actual)
    return _ratio(relevant, len(actual), empty=1.0)


def _stale(item: Mapping[str, Any], decision_time) -> bool:
    status = str(item.get("quality_status", item.get("status", ""))).upper()
    if status in {"STALE", "REJECTED"}:
        return True
    available = item.get("available_at")
    if available is not None:
        from datetime import datetime
        if isinstance(available, str):
            try:
                available = datetime.fromisoformat(available.replace("Z", "+00:00"))
            except ValueError:
                return True
        if isinstance(available, datetime) and available > decision_time:
            return True
    return False


def metric_details(case: DecisionEvalCase, output: Any, *, stability: float | None = None) -> dict[str, MetricValue]:
    data = _mapping(output)
    required = set(case.expected.required_evidence_types)
    actual_types = _types(data)
    items = _items(data)
    recall = _ratio(len(required & set(actual_types)), len(required), empty=1.0)
    precision = _ratio(sum(item in required for item in actual_types), len(actual_types), empty=1.0)
    stale = _ratio(sum(_stale(item, case.decision_time) for item in items), len(items), empty=0.0)
    claims = data.get("claims", [])
    if isinstance(claims, Mapping):
        claims = list(claims.values())
    claims = claims if isinstance(claims, Sequence) and not isinstance(claims, (str, bytes)) else []
    produced_refs = _refs(data)
    unsupported = 0
    for claim in claims:
        # Only explicit evidence_refs are accepted.  No text/NLP inference.
        refs = claim.get("evidence_refs") if isinstance(claim, Mapping) else None
        if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)) or not refs or not set(map(str, refs)).issubset(produced_refs):
            unsupported += 1
    tools = data.get("tool_calls", [])
    tools = tools if isinstance(tools, Sequence) and not isinstance(tools, (str, bytes)) else []
    tool_success = sum(bool(call.get("success")) or str(call.get("status", "")).upper() in {"OK", "SUCCESS", "SUCCEEDED"} for call in tools if isinstance(call, Mapping))
    specialists = data.get("specialists", [])
    if isinstance(specialists, Mapping):
        specialists = [dict(value, name=str(name)) if isinstance(value, Mapping) else value for name, value in specialists.items()]
    specialists = specialists if isinstance(specialists, Sequence) and not isinstance(specialists, (str, bytes)) else []
    specialist_ok = sum(str(item.get("status", "")).upper() in {"OK", "SUCCESS", "SUCCEEDED", "COMPLETED"} or item.get("completed") is True for item in specialists if isinstance(item, Mapping))
    expected_specialists = set(case.expected.required_specialists)
    if expected_specialists:
        completed_names = {str(item.get("name", item.get("specialist", ""))) for item in specialists if isinstance(item, Mapping) and (str(item.get("status", "")).upper() in {"OK", "SUCCESS", "SUCCEEDED", "COMPLETED"} or item.get("completed") is True)}
        specialist_value = _ratio(len(expected_specialists & completed_names), len(expected_specialists), empty=0.0)
    else:
        expected_count = data.get("expected_specialist_count")
        specialist_value = _ratio(specialist_ok, int(expected_count), empty=0.0) if expected_count is not None else _ratio(specialist_ok, len(specialists), empty=0.0)
    violations = data.get("contract_violations", [])
    violation_count = int(bool(violations))
    flags = data.get("risk_flags", [])
    flags = set(str(v) for v in flags) if isinstance(flags, Sequence) and not isinstance(flags, (str, bytes)) else set()
    expected_flags = set(case.expected.risk_flags)
    output_weight = data.get("target_weight")
    final = data.get("final_decision")
    if isinstance(final, Mapping):
        action_value = final.get("decision_action", final.get("investment_action", final.get("action", "")))
        if output_weight is None:
            output_weight = final.get("target_weight")
    else:
        action_value = final
    action = str(data.get("decision_action", data.get("action", action_value or ""))).upper()
    veto = action == "VETO" or data.get("veto") is True
    expected_veto = case.expected.requires_veto or bool(expected_flags & {"VETO", "MUST_VETO", "REJECT"})
    portfolio = data.get("portfolio_constraint_violations", data.get("portfolio_constraint_violation", []))
    portfolio_count = len(portfolio) if isinstance(portfolio, Sequence) and not isinstance(portfolio, (str, bytes)) and not isinstance(portfolio, (str, bytes)) else int(bool(portfolio))
    weight_violation = output_weight is not None and case.expected.max_target_weight is not None and float(output_weight) > case.expected.max_target_weight
    forbidden = set(case.expected.forbidden_claims)
    forbidden_hits = 0
    for claim in claims:
        if not isinstance(claim, Mapping):
            continue
        # A forbidden claim is matched only against an explicit claim_id or
        # exact claim value; prose similarity is intentionally out of scope.
        if claim.get("claim_id") in forbidden or claim.get("claim") in forbidden:
            forbidden_hits += 1
    acceptable = set(case.expected.acceptable_actions)
    action_ok = int(not acceptable or action in acceptable)
    outcome = data.get("outcome") if isinstance(data.get("outcome"), Mapping) else data
    confidence = data.get("confidence")
    actual = data.get("outcome_label", data.get("correct"))
    if actual is None and isinstance(outcome, Mapping):
        actual = outcome.get("hit", outcome.get("outcome_label"))
    if isinstance(actual, bool):
        actual = float(actual)
    if actual is not None:
        actual = float(actual)
    confidence_value = float(confidence) if confidence is not None else None
    if confidence_value is not None and not 0 <= confidence_value <= 1:
        raise ValueError("confidence must be between 0 and 1")
    if actual is not None and actual not in {0.0, 1.0}:
        raise ValueError("calibration outcome must be binary")
    return {
        "evidence_recall": recall,
        "evidence_precision": precision,
        "critical_evidence_coverage": recall,
        "stale_evidence_rate": stale,
        "unsupported_claim_rate": _ratio(unsupported, len(claims), empty=0.0),
        "tool_success_rate": _ratio(tool_success, len(tools), empty=0.0),
        "specialist_completion_rate": specialist_value,
        "contract_violation_rate": _ratio(violation_count, 1, empty=0.0),
        "decision_stability": MetricValue(stability, 1 if stability is not None else 0, 1 if stability is not None else 0, stability is not None),
        "risk_recall": _ratio(len(expected_flags & flags), len(expected_flags), empty=1.0),
        "false_veto_rate": _ratio(int(veto and not expected_veto), int(not expected_veto), empty=0.0),
        "missed_veto_rate": _ratio(int(expected_veto and not veto), int(expected_veto), empty=0.0),
        "portfolio_constraint_violation_rate": _ratio(int(bool(portfolio_count or weight_violation)), 1, empty=0.0),
        "return": _outcome_metric(outcome, "return_pct", "return"),
        "excess_return": _outcome_metric(outcome, "excess_return_pct", "excess_return"),
        "drawdown": _outcome_metric(outcome, "max_drawdown", "drawdown"),
        "hit_rate": MetricValue(float(actual) if actual is not None else None, 1 if actual is not None else 0, 1 if actual is not None else 0, actual is not None),
        "confidence_calibration": MetricValue(1 - abs(confidence_value - actual), 1, 1, True) if confidence_value is not None and actual is not None else MetricValue(None, 0, 0, False),
        "brier_score": MetricValue((confidence_value - actual) ** 2, 1, 1, True) if confidence_value is not None and actual is not None else MetricValue(None, 0, 0, False),
        # ECE is a dataset-level binned statistic; it is undefined for one
        # case and is populated only by aggregate_metrics().
        "ece": MetricValue(None, 0, 0, False),
        "forbidden_claim_rate": _ratio(forbidden_hits, len(claims), empty=0.0),
        "acceptable_action_rate": MetricValue(float(action_ok), 1, 1, True),
        "target_weight_constraint_violation_rate": MetricValue(float(weight_violation), 1, 1, True),
    }


def _outcome_metric(outcome: Mapping[str, Any], key: str, *aliases: str) -> MetricValue:
    value = outcome.get(key)
    if value is None:
        for alias in aliases:
            value = outcome.get(alias)
            if value is not None:
                break
    if value is None:
        return MetricValue(None, 0, 0, False)
    value = float(value)
    if not isfinite(value):
        raise ValueError(f"{key} must be finite")
    return MetricValue(value, value, 1, True)


def compute_metrics(case: DecisionEvalCase, output: Any, *, stability: float | None = None) -> dict[str, float | None]:
    return {name: detail.value for name, detail in metric_details(case, output, stability=stability).items()}


def aggregate_metrics(cases: Sequence[DecisionEvalCase], outputs: Sequence[Any], *, stability: Sequence[float | None] | None = None) -> dict[str, float | None]:
    """Aggregate case metrics, with calibration computed over fixed bins.

    ECE uses ten deterministic bins ``[0,.1), ... [.9,1]`` and compares the
    mean confidence with the mean observed binary outcome in each non-empty
    bin. Brier and confidence calibration are means over all labelled pairs.
    """
    if len(cases) != len(outputs):
        raise ValueError("cases and outputs must have equal lengths")
    details = [metric_details(case, output, stability=(stability[index] if stability is not None else None)) for index, (case, output) in enumerate(zip(cases, outputs))]
    values: dict[str, float | None] = {}
    for name in METRIC_NAMES:
        if name in {"ece", "brier_score", "confidence_calibration"}:
            continue
        nums = [item[name].value for item in details if item[name].defined and item[name].value is not None]
        values[name] = sum(nums) / len(nums) if nums else None
    pairs = []
    for output in outputs:
        data = _mapping(output)
        nested = data.get("outcome") if isinstance(data.get("outcome"), Mapping) else data
        confidence = data.get("confidence")
        outcome = data.get("outcome_label", data.get("correct"))
        if outcome is None and isinstance(nested, Mapping):
            outcome = nested.get("outcome_label", nested.get("correct", nested.get("hit")))
        if isinstance(outcome, bool):
            outcome = float(outcome)
        if confidence is not None and outcome is not None:
            pairs.append((float(confidence), float(outcome)))
    if not pairs:
        values.update({"brier_score": None, "confidence_calibration": None, "ece": None})
    else:
        values["brier_score"] = sum((confidence - outcome) ** 2 for confidence, outcome in pairs) / len(pairs)
        values["confidence_calibration"] = sum(1 - abs(confidence - outcome) for confidence, outcome in pairs) / len(pairs)
        bins: list[list[tuple[float, float]]] = [[] for _ in range(10)]
        for confidence, outcome in pairs:
            bins[min(9, int(confidence * 10))].append((confidence, outcome))
        values["ece"] = sum((len(bucket) / len(pairs)) * abs(sum(c for c, _ in bucket) / len(bucket) - sum(o for _, o in bucket) / len(bucket)) for bucket in bins if bucket)
    return values


__all__ = ["METRIC_NAMES", "MetricValue", "aggregate_metrics", "compute_metrics", "evidence_recall", "evidence_precision", "metric_details"]
