"""MCP Backtest Server（收尾文档 §30 / §33）。

Agent MCP -> QuantClient.create_backtest() -> Quant backtest.v1。
调用模式：create backtest -> backtest_id -> poll/status -> consume result。
不再直接调用 engines.backtest；不同步阻塞执行长时间回测。
"""
from __future__ import annotations

import time
from typing import Any

from services.subsystems import get_quant_client


def create_backtest(config: dict[str, Any]) -> dict[str, Any]:
    return get_quant_client().create_backtest(config)


def get_backtest(backtest_id: str) -> dict[str, Any]:
    return get_quant_client().get_backtest(backtest_id)


def wait_backtest(backtest_id: str, timeout: float = 120.0, poll_interval: float = 0.5) -> dict[str, Any]:
    """轮询直到 COMPLETED / FAILED（§30：poll/status -> consume result）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = get_quant_client().get_backtest(backtest_id)
        if payload.get("status") in {"COMPLETED", "FAILED"}:
            return payload
        time.sleep(poll_interval)
    raise TimeoutError(f"backtest {backtest_id} did not finish within {timeout}s")


def run_backtest(config: dict[str, Any], wait: bool = True, timeout: float = 120.0) -> dict[str, Any]:
    created = create_backtest(config)
    if not wait:
        return created
    return wait_backtest(created["backtest_id"], timeout=timeout)


def get_backtest_metrics(backtest_id: str) -> dict[str, Any]:
    return get_quant_client().get_backtest_metrics(backtest_id)


def get_backtest_trades(backtest_id: str) -> dict[str, Any]:
    return get_quant_client().get_backtest_trades(backtest_id)
