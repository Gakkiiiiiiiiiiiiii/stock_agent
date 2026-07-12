from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScheduledTask:
    name: str
    cron: str
    description: str


DEFAULT_TASKS = [
    ScheduledTask("pre_market_scan", "30 8 * * 1-5", "盘前市场扫描"),
    ScheduledTask("midday_scan", "45 11 * * 1-5", "午间市场扫描"),
    ScheduledTask("after_close_scan", "30 15 * * 1-5", "盘后市场扫描"),
]


def list_scheduled_tasks() -> list[ScheduledTask]:
    return DEFAULT_TASKS

