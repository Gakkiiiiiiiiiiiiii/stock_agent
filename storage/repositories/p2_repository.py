from __future__ import annotations

from sqlalchemy import select

from storage.db import session_scope
from storage.models.p2 import (
    AgentConflictRecord, AgentRun, AgentSubtask, ExecutionFillRecord,
    ExecutionOrderRecord, ExecutionReconciliationRecord, PositionSnapshotRecord,
    SkillEvaluationRecord, SkillProposalRecord, StrategyDefinitionRecord, TradeIntentRecord, ExecutionOrderEventRecord, ExecutionRuntimeState,
)


class P2Repository:
    """One persistence adapter for P2 records; domain services keep pure logic."""
    def create_agent_run(self, **payload) -> AgentRun:
        return self._add(AgentRun(**payload))

    def add_subtask(self, **payload) -> AgentSubtask:
        return self._add(AgentSubtask(**payload))

    def add_conflict(self, **payload) -> AgentConflictRecord:
        return self._add(AgentConflictRecord(**payload))

    def update_agent_run(self, run_id: str, **payload) -> AgentRun:
        with session_scope() as session:
            row = session.get(AgentRun, run_id)
            if row is None:
                raise KeyError(run_id)
            for key, value in payload.items():
                setattr(row, key, value)
            session.flush()
            session.refresh(row)
            return row

    def create_trade_intent(self, **payload) -> TradeIntentRecord:
        return self._add(TradeIntentRecord(**payload))

    def add_order(self, **payload) -> ExecutionOrderRecord:
        return self._add(ExecutionOrderRecord(**payload))

    def add_fill(self, **payload) -> ExecutionFillRecord:
        return self._add(ExecutionFillRecord(**payload))

    def add_order_event(self, **payload) -> ExecutionOrderEventRecord:
        return self._add(ExecutionOrderEventRecord(**payload))

    def get_trade_intent_by_client_order_id(self, client_order_id: str) -> TradeIntentRecord | None:
        with session_scope() as session:
            return session.execute(select(TradeIntentRecord).where(TradeIntentRecord.client_order_id == client_order_id)).scalars().first()

    def get_order(self, order_id: str) -> ExecutionOrderRecord | None:
        with session_scope() as session:
            return session.get(ExecutionOrderRecord, order_id)

    def get_order_for_client_order_id(self, client_order_id: str) -> ExecutionOrderRecord | None:
        with session_scope() as session:
            intent = session.execute(select(TradeIntentRecord).where(TradeIntentRecord.client_order_id == client_order_id)).scalars().first()
            return session.execute(select(ExecutionOrderRecord).where(ExecutionOrderRecord.trade_intent_id == intent.id)).scalars().first() if intent else None

    def update_order_status(self, order_id: str, status: str, **payload) -> ExecutionOrderRecord:
        with session_scope() as session:
            row = session.get(ExecutionOrderRecord, order_id)
            if row is None: raise KeyError(order_id)
            row.status = status
            for key, value in payload.items(): setattr(row, key, value)
            session.flush(); session.refresh(row)
            return row

    def list_fills(self, order_id: str) -> list[ExecutionFillRecord]:
        with session_scope() as session:
            return list(session.execute(select(ExecutionFillRecord).where(ExecutionFillRecord.execution_order_id == order_id)).scalars())

    def list_open_orders(self) -> list[ExecutionOrderRecord]:
        terminal = {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}
        with session_scope() as session:
            return list(session.execute(select(ExecutionOrderRecord).where(ExecutionOrderRecord.status.not_in(terminal))).scalars())

    def add_position_snapshot(self, **payload) -> PositionSnapshotRecord:
        return self._add(PositionSnapshotRecord(**payload))

    def add_reconciliation(self, **payload) -> ExecutionReconciliationRecord:
        return self._add(ExecutionReconciliationRecord(**payload))

    def get_execution_runtime_state(self) -> ExecutionRuntimeState | None:
        with session_scope() as session:
            return session.get(ExecutionRuntimeState, 1)

    def set_execution_runtime_state(self, halted: bool, halt_reason: str | None = None) -> ExecutionRuntimeState:
        with session_scope() as session:
            row = session.get(ExecutionRuntimeState, 1)
            if row is None:
                row = ExecutionRuntimeState(id=1)
                session.add(row)
            row.halted = bool(halted)
            row.halt_reason = halt_reason
            session.flush()
            session.refresh(row)
            return row

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
