from __future__ import annotations

from agent.contracts import AgentRole, AgentTask
from agent.specialists.base import ToolSpecialist


class MarketSpecialist(ToolSpecialist):
    role = AgentRole.MARKET
    def __call__(self, task: AgentTask, _shared):
        result = {name: self.call(name) for name in ("get_market_features", "get_market_regime", "get_sector_strength")}
        return self.artifact(task, result, tool_calls=3)
