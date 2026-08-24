from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any, Literal

from evals.datasets import DecisionEvalCase
from evals.metrics import aggregate_metrics, compute_metrics


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"value is not JSON serializable: {type(value).__name__}")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(_dump(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _identity(value: Any, name: str) -> tuple[str, str]:
    if value is None:
        raise ValueError(f"{name} is mandatory for offline evaluation")
    data = _dump(value)
    if not isinstance(data, Mapping):
        raise TypeError(f"{name} must be a contract/model or mapping")
    identifier = data.get("bundle_id" if name == "bundle" else "snapshot_id")
    digest = data.get("bundle_hash" if name == "bundle" else "snapshot_hash")
    if not identifier or not digest:
        raise ValueError(f"{name} must expose immutable id and hash")
    return str(identifier), str(digest)


def _validate_snapshot_binding(snapshot: Any, bundle_id: str, bundle_hash: str) -> None:
    payload = _dump(snapshot)
    reference = payload.get("input_bundle") if isinstance(payload, Mapping) else None
    if isinstance(reference, Mapping):
        if str(reference.get("bundle_id")) != bundle_id or str(reference.get("bundle_hash")) != bundle_hash:
            raise ValueError("snapshot input_bundle does not match fixed bundle")


@dataclass(frozen=True)
class EvalContext:
    case: DecisionEvalCase
    bundle: Any
    snapshot: Any
    proposal: Any | None
    replay_kind: Literal["model", "skill", "policy"]


@dataclass(frozen=True)
class Variant:
    name: str
    adapter: Callable[[EvalContext], Any]

    def __post_init__(self) -> None:
        if not self.name.strip() or not callable(self.adapter):
            raise ValueError("variant requires a nonblank name and callable adapter")


@dataclass(frozen=True)
class EvalError:
    code: str
    message: str
    exception_type: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "exception_type": self.exception_type}


@dataclass(frozen=True)
class EvalResult:
    case_id: str
    variant: str
    status: Literal["OK", "ERROR"]
    bundle_id: str
    bundle_hash: str
    snapshot_id: str
    snapshot_hash: str
    output: Mapping[str, Any] | None
    metrics: Mapping[str, float | None]
    errors: tuple[EvalError, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id, "variant": self.variant, "status": self.status,
            "bundle_id": self.bundle_id, "bundle_hash": self.bundle_hash,
            "snapshot_id": self.snapshot_id, "snapshot_hash": self.snapshot_hash,
            "output": _dump(self.output) if self.output is not None else None,
            "metrics": dict(sorted(self.metrics.items())),
            "errors": [error.as_dict() for error in self.errors],
        }


@dataclass(frozen=True)
class ComparisonReport:
    kind: str
    variant_a: str
    variant_b: str
    results_a: tuple[EvalResult, ...]
    results_b: tuple[EvalResult, ...]
    aggregate_a: Mapping[str, float | None]
    aggregate_b: Mapping[str, float | None]
    deltas: Mapping[str, float | None]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "variant_a": self.variant_a, "variant_b": self.variant_b,
            "results_a": [item.as_dict() for item in self.results_a],
            "results_b": [item.as_dict() for item in self.results_b],
            "aggregate_a": dict(sorted(self.aggregate_a.items())),
            "aggregate_b": dict(sorted(self.aggregate_b.items())),
            "deltas": dict(sorted(self.deltas.items())),
        }


class EvalRunner:
    """Run pure adapters against fixed inputs only.

    The runner intentionally has no fallback path: adapter exceptions become
    explicit error records, while identity/hash violations are errors too.
    No client, clock, filesystem dataset, or latest-data lookup is available
    through the context.
    """

    def __init__(self, cases: Any):
        self._cases = tuple(sorted(getattr(cases, "cases", cases), key=lambda case: case.case_id))

    def evaluate_case(self, case: DecisionEvalCase, variant: Variant, *, bundle: Any, snapshot: Any, proposal: Any | None = None, kind: str = "model") -> EvalResult:
        if kind not in {"model", "skill", "policy"}:
            raise ValueError("kind must be model, skill, or policy")
        bundle_id, bundle_hash = _identity(bundle, "bundle")
        snapshot_id, snapshot_hash = _identity(snapshot, "snapshot")
        _validate_snapshot_binding(snapshot, bundle_id, bundle_hash)
        if case.bundle_id != bundle_id:
            raise ValueError(f"case {case.case_id} expects bundle {case.bundle_id}, got {bundle_id}")
        if kind == "policy" and proposal is None:
            raise ValueError("policy evaluation requires a fixed proposal")
        proposal_digest = _digest(proposal) if proposal is not None else None
        context = EvalContext(case=case, bundle=bundle, snapshot=snapshot, proposal=proposal, replay_kind=kind)  # type: ignore[arg-type]
        try:
            output = variant.adapter(context)
            # Detect attempted mutation of fixed contract state even when the
            # adapter catches its own exception or returns a different object.
            after_bundle_id, after_bundle_hash = _identity(bundle, "bundle")
            after_snapshot_id, after_snapshot_hash = _identity(snapshot, "snapshot")
            if (after_bundle_id, after_bundle_hash) != (bundle_id, bundle_hash) or (after_snapshot_id, after_snapshot_hash) != (snapshot_id, snapshot_hash):
                raise ValueError("adapter mutated fixed bundle or snapshot identity")
            if proposal is not None:
                if _digest(proposal) != proposal_digest:
                    raise ValueError("adapter mutated fixed proposal")
            output_data = _dump(output)
            if not isinstance(output_data, Mapping):
                raise TypeError("variant adapter must return a mapping or Pydantic model")
            for id_key, expected in (("bundle_id", bundle_id), ("bundle_hash", bundle_hash), ("snapshot_id", snapshot_id), ("snapshot_hash", snapshot_hash)):
                if id_key in output_data and str(output_data[id_key]) != expected:
                    raise ValueError(f"adapter output {id_key} does not match fixed input")
            # A returned replacement bundle/proposal is not a valid comparison
            # result even if its top-level IDs were omitted.
            for key, expected, name in (("bundle", bundle, "bundle"), ("snapshot", snapshot, "snapshot"), ("proposal", proposal, "proposal")):
                if key in output_data and output_data[key] is not None and _digest(output_data[key]) != _digest(expected):
                    raise ValueError(f"adapter returned a changed fixed {name}")
            metrics = compute_metrics(case, output_data)
            return EvalResult(case.case_id, variant.name, "OK", bundle_id, bundle_hash, snapshot_id, snapshot_hash, dict(output_data), metrics)
        except Exception as exc:
            error = EvalError("EVAL_ADAPTER_ERROR", str(exc), type(exc).__name__)
            return EvalResult(case.case_id, variant.name, "ERROR", bundle_id, bundle_hash, snapshot_id, snapshot_hash, None, {}, (error,))

    # Explicit aliases keep call sites readable while preserving one guarded
    # execution path for all replay kinds.
    run = evaluate_case

    @staticmethod
    def _resolve(source: Any, case: DecisionEvalCase, name: str) -> Any:
        if callable(source):
            value = source(case)
        elif isinstance(source, Mapping) and ("bundle_id" not in source and "snapshot_id" not in source and "proposal_id" not in source):
            try:
                value = source[case.bundle_id]
            except KeyError as exc:
                raise ValueError(f"no fixed {name} supplied for bundle {case.bundle_id}") from exc
        else:
            value = source
        if value is None:
            raise ValueError(f"fixed {name} is mandatory")
        return value

    def compare(self, cases: Any, variant_a: Variant, variant_b: Variant, *, bundle: Any, snapshot: Any, proposal: Any | None = None, kind: str = "model") -> ComparisonReport:
        cases_ordered = tuple(sorted(getattr(cases, "cases", cases), key=lambda case: case.case_id))
        results_a = tuple(self.evaluate_case(case, variant_a, bundle=self._resolve(bundle, case, "bundle"), snapshot=self._resolve(snapshot, case, "snapshot"), proposal=self._resolve(proposal, case, "proposal") if proposal is not None else None, kind=kind) for case in cases_ordered)
        results_b = tuple(self.evaluate_case(case, variant_b, bundle=self._resolve(bundle, case, "bundle"), snapshot=self._resolve(snapshot, case, "snapshot"), proposal=self._resolve(proposal, case, "proposal") if proposal is not None else None, kind=kind) for case in cases_ordered)
        # Stability is a paired comparison metric, not a property inferred
        # from one output.  The fixed decision signature is explicit.
        paired_a: list[EvalResult] = []
        paired_b: list[EvalResult] = []
        for left, right in zip(results_a, results_b):
            stable = None
            if left.status == right.status == "OK":
                stable = float(_decision_signature(left.output) == _decision_signature(right.output))
            paired_a.append(_replace_stability(left, stable))
            paired_b.append(_replace_stability(right, stable))
        results_a, results_b = tuple(paired_a), tuple(paired_b)
        aggregate_a = _aggregate(results_a, cases_ordered)
        aggregate_b = _aggregate(results_b, cases_ordered)
        deltas = {key: (aggregate_b.get(key) - aggregate_a.get(key)) if aggregate_a.get(key) is not None and aggregate_b.get(key) is not None else None for key in sorted(set(aggregate_a) | set(aggregate_b))}
        return ComparisonReport(kind, variant_a.name, variant_b.name, results_a, results_b, aggregate_a, aggregate_b, deltas)

    def compare_model(self, cases: Any, variant_a: Variant, variant_b: Variant, **inputs: Any) -> ComparisonReport:
        return self.compare(cases, variant_a, variant_b, kind="model", **inputs)

    def compare_skill(self, cases: Any, variant_a: Variant, variant_b: Variant, **inputs: Any) -> ComparisonReport:
        return self.compare(cases, variant_a, variant_b, kind="skill", **inputs)

    def compare_policy(self, cases: Any, variant_a: Variant, variant_b: Variant, **inputs: Any) -> ComparisonReport:
        return self.compare(cases, variant_a, variant_b, kind="policy", **inputs)


def _replace_stability(result: EvalResult, value: float | None) -> EvalResult:
    metrics = dict(result.metrics)
    metrics["decision_stability"] = value
    return EvalResult(result.case_id, result.variant, result.status, result.bundle_id, result.bundle_hash, result.snapshot_id, result.snapshot_hash, result.output, metrics, result.errors)


def _decision_signature(output: Mapping[str, Any] | None) -> str:
    if output is None:
        return ""
    # Runtime outputs commonly wrap the immutable decision under
    # ``final_decision`` or ``decision``.  Comparing only top-level fields
    # would make two different nested decisions look identical.
    relevant: Any = output
    for key in ("final_decision", "decision", "result"):
        candidate = relevant.get(key) if isinstance(relevant, Mapping) else None
        if isinstance(candidate, Mapping):
            relevant = candidate
            break
    if isinstance(relevant, Mapping):
        nested = relevant.get("final_decision") or relevant.get("decision")
        if isinstance(nested, Mapping):
            relevant = nested
    if relevant is output:
        relevant = {key: output.get(key) for key in ("decision_action", "action", "investment_action", "target_weight", "weight_delta", "veto", "policy_result_id", "decision_hash") if key in output}
    return json.dumps(_dump(relevant), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _aggregate(results: tuple[EvalResult, ...], cases: tuple[DecisionEvalCase, ...]) -> dict[str, float | None]:
    keys = sorted({key for result in results for key in result.metrics})
    aggregate: dict[str, float | None] = {}
    for key in keys:
        values = [result.metrics[key] for result in results if result.status == "OK" and result.metrics.get(key) is not None]
        aggregate[key] = sum(values) / len(values) if values else None
    outputs = [result.output for result in results if result.status == "OK" and result.output is not None]
    ok_cases = [case for case, result in zip(cases, results) if result.status == "OK" and result.output is not None]
    if outputs:
        aggregate.update(aggregate_metrics(ok_cases, outputs, stability=[result.metrics.get("decision_stability") for result in results if result.status == "OK" and result.output is not None]))
    return aggregate


def compare_variants(cases: Any, variant_a: Variant, variant_b: Variant, *, bundle: Any, snapshot: Any, proposal: Any | None = None, kind: str = "model") -> ComparisonReport:
    return EvalRunner(cases).compare(cases, variant_a, variant_b, bundle=bundle, snapshot=snapshot, proposal=proposal, kind=kind)


__all__ = ["ComparisonReport", "EvalContext", "EvalError", "EvalResult", "EvalRunner", "Variant", "compare_variants"]
