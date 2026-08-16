from __future__ import annotations

from agent.contracts import AgentRole, AgentTask
from agent.task_graph import TaskGraph


def build_daily_market_decision_graph(objective: str, **budgets) -> TaskGraph:
    graph = TaskGraph()
    root = {"task_type": "daily_market_decision", "objective": objective, **budgets}
    market = AgentTask(assigned_agent=AgentRole.MARKET, **root)
    research = AgentTask(assigned_agent=AgentRole.RESEARCH, **root)
    technical = AgentTask(assigned_agent=AgentRole.TECHNICAL, **root)
    # 设计文档 §24：Factor Specialist 并行调用 stock_factor /api/v1/alpha/score。
    factor = AgentTask(assigned_agent=AgentRole.FACTOR, **root)
    portfolio = AgentTask(assigned_agent=AgentRole.PORTFOLIO, **root)
    risk = AgentTask(assigned_agent=AgentRole.RISK, **root)
    for task in (market, research, technical, factor): graph.add_task(task)
    graph.add_task(portfolio, depends_on=[market.task_id, research.task_id, technical.task_id, factor.task_id])
    graph.add_task(risk, depends_on=[portfolio.task_id])
    return graph
