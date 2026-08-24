"""Deterministic, offline evaluation of fixed decision inputs.

The package deliberately has no clients, model gateways, tool calls, or data
directory dependencies.  Adapters receive only :class:`EvalContext` objects
created by the runner.
"""

from evals.datasets import DecisionEvalCase, EvalExpected, load_jsonl
from evals.metrics import compute_metrics
from evals.runners import EvalContext, EvalRunner, Variant, compare_variants

__all__ = [
    "DecisionEvalCase",
    "EvalExpected",
    "EvalContext",
    "EvalRunner",
    "Variant",
    "compare_variants",
    "compute_metrics",
    "load_jsonl",
]
