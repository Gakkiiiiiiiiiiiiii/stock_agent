"""Reconciliation is a stop-the-line guard for automatic position increases."""
from __future__ import annotations


def reconcile(local: dict, broker: dict, tolerance: float = 1e-6) -> dict:
    differences: list[dict] = []
    for field in ("cash",):
        if abs(float(local.get(field, 0)) - float(broker.get(field, 0))) > tolerance:
            differences.append({"kind": field.upper() + "_MISMATCH", "local": local.get(field, 0), "broker": broker.get(field, 0)})
    local_positions = dict(local.get("positions") or {})
    broker_positions = dict(broker.get("positions") or {})
    for symbol in sorted(set(local_positions) | set(broker_positions)):
        if abs(float(local_positions.get(symbol, 0)) - float(broker_positions.get(symbol, 0))) > tolerance:
            differences.append({"kind": "POSITION_MISMATCH", "symbol": symbol, "local": local_positions.get(symbol, 0), "broker": broker_positions.get(symbol, 0)})
    return {"status": "RECONCILED" if not differences else "RECONCILIATION_REQUIRED", "differences": differences, "allow_position_increase": not differences}
