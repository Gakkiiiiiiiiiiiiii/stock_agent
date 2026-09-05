"""Deterministic formal calculator benchmark (no network or live execution)."""
from __future__ import annotations

import time

from app.application.decision.formal_calculator import FormalDecisionCalculator


def run(iterations: int = 1000) -> dict[str, float | str]:
    bundle = {"bundle_hash": "fixture", "market": {}, "factor": {}, "content": {}}
    calculator = FormalDecisionCalculator()
    start = time.perf_counter()
    result = None
    for _ in range(iterations):
        result = calculator.calculate(bundle)
    elapsed = time.perf_counter() - start
    return {"iterations": iterations, "elapsed_seconds": elapsed, "avg_ms": elapsed * 1000 / iterations, "output_status": result["status"] if result else ""}


if __name__ == "__main__":
    print(run())
