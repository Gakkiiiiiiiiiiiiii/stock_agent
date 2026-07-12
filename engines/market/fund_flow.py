from __future__ import annotations


def summarize_fund_flow(records: list[dict]) -> dict:
    net = sum(float(item.get("net_inflow", 0)) for item in records)
    return {"net_inflow": net, "direction": "inflow" if net > 0 else "outflow" if net < 0 else "flat"}

