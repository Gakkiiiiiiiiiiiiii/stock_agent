from __future__ import annotations

from storage.db import session_scope
from storage.models.p2 import (
    AgentConflictRecord, AgentRun, AgentSubtask, ExecutionFillRecord,
    ExecutionOrderRecord, ExecutionReconciliationRecord, PositionSnapshotRecord,
    SkillEvaluationRecord, SkillProposalRecord, StrategyDefinitionRecord, TradeIntentRecord,
)


class P2Repository:
    """One persistence adapter for P2 records; domain services keep pure logic."""
    def create_agent_run(self, **payload) -> AgentRun:
        return self._add(AgentRun(**payload))

    def add_subtask(self, **payload) -> AgentSubtask:
        return self._add(AgentSubtask(**payload))

    def add_conflict(self, **payload) -> AgentConflictRecord:
        return self._add(AgentConflictRecord(**payload))

    def create_trade_intent(self, **payload) -> TradeIntentRecord:
        return self._add(TradeIntentRecord(**payload))

    def add_order(self, **payload) -> ExecutionOrderRecord:
        return self._add(ExecutionOrderRecord(**payload))

    def add_fill(self, **payload) -> ExecutionFillRecord:
        return self._add(ExecutionFillRecord(**payload))

    def add_position_snapshot(self, **payload) -> PositionSnapshotRecord:
        return self._add(PositionSnapshotRecord(**payload))

    def add_reconciliation(self, **payload) -> ExecutionReconciliationRecord:
        return self._add(ExecutionReconciliationRecord(**payload))

    def create_skill_proposal(self, **payload) -> SkillProposalRecord:
        return self._add(SkillProposalRecord(**payload))

    def add_skill_evaluation(self, **payload) -> SkillEvaluationRecord:
        return self._add(SkillEvaluationRecord(**payload))

    def save_strategy(self, **payload) -> StrategyDefinitionRecord:
        return self._add(StrategyDefinitionRecord(**payload))

    @staticmethod
    def _add(row):
        with session_scope() as session:
            session.add(row)
            session.flush()
            session.refresh(row)
            return row
