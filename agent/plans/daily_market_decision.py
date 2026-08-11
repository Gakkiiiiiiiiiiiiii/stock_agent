from __future__ import annotations

from agent.contracts import AgentRole, AgentTask
from agent.task_graph import TaskGraph


def build_daily_market_decision_graph(objective: str, **budgets) -> TaskGraph:
    graph = TaskGraph()
    root = {"task_type": "daily_market_decision", "objective": objective, **budgets}
    market = AgentTask(assigned_agent=AgentRole.MARKET, **root)
    research = AgentTask(assigned_agent=AgentRole.RESEARCH, **root)
    technical = AgentTask(assigned_agent=AgentRole.TECHNICAL, **root)
    portfolio = AgentTask(assigned_agent=AgentRole.PORTFOLIO, **root)
    risk = AgentTask(assigned_agent=AgentRole.RISK, **root)
    # A factor-score specialist is deliberately omitted until an online factor
    # score service exists; a synthetic score would be worse than no input.
    for task in (market, research, technical): graph.add_task(task)
    graph.add_task(portfolio, depends_on=[market.task_id, research.task_id, technical.task_id])
    graph.add_task(risk, depends_on=[portfolio.task_id])
    return graph
