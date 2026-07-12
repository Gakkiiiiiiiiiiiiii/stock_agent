from __future__ import annotations


def summarize_strategy_performance(traces: list[dict]) -> dict:
    if not traces:
        return {"count": 0, "avg_return_5d": 0.0, "success_rate": None}
    avg = sum(item.get("next_5d_return", 0) for item in traces) / len(traces)
    success_rate = sum(1 for item in traces if item.get("success_label")) / len(traces)
    return {"count": len(traces), "avg_return_5d": round(avg, 4), "success_rate": round(success_rate, 4)}

