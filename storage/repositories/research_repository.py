from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import or_, select

from storage.db import session_scope
from storage.models.research import (
    DecisionReview as LegacyDecisionReview,
    DecisionSnapshot,
    InvestmentDecision,
    InvestmentDecisionOutcome,
    MarketRegimeHistory,
    MarketRegimeState,
    DecisionSnapshotV3Record,
    DecisionOutcomeRecord,
    DecisionReviewV2Record,
    DecisionMemoryRecord,
    SpecialistArtifactRecord,
    InvestmentProposalV2Record,
    PolicyEvaluationRecord,
    DecisionBundleBindingRecord,
)
from contracts.decision_snapshot import DecisionSnapshotV3
from contracts.outcome import DecisionOutcome
from contracts.review import DecisionReview
from contracts.decision_memory import DecisionMemory
from contracts.proposal import InvestmentProposalV2
from contracts.decision import PolicyEvaluation
from storage.models.decision_input import DecisionInputBundleRecord


class MarketRegimeRepository:
    def get_state(self, market_code: str) -> MarketRegimeState | None:
        with session_scope() as session:
            return session.get(MarketRegimeState, market_code)

    def save_state(self, state: MarketRegimeState) -> MarketRegimeState:
        with session_scope() as session:
            session.merge(state)
            session.flush()
            return session.get(MarketRegimeState, state.market_code)

    def add_history(self, **payload) -> MarketRegimeHistory:
        with session_scope() as session:
            item = MarketRegimeHistory(**payload)
            session.add(item)
            session.flush()
            session.refresh(item)
            return item

    def close_active_history(self, market_code: str, ended_at: date) -> None:
        with session_scope() as session:
            active = session.execute(
                select(MarketRegimeHistory).where(MarketRegimeHistory.market_code == market_code, MarketRegimeHistory.ended_at.is_(None)).order_by(MarketRegimeHistory.started_at.desc())
            ).scalars().first()
            if active is not None:
                active.ended_at = ended_at
                session.add(active)

    def list_history(self, market_code: str, limit: int = 30, start_date: date | None = None, end_date: date | None = None) -> list[MarketRegimeHistory]:
        with session_scope() as session:
            query = select(MarketRegimeHistory).where(MarketRegimeHistory.market_code == market_code)
            if start_date:
                query = query.where((MarketRegimeHistory.ended_at.is_(None)) | (MarketRegimeHistory.ended_at >= start_date))
            if end_date:
                query = query.where(MarketRegimeHistory.started_at <= end_date)
            return list(session.execute(query.order_by(MarketRegimeHistory.started_at.desc()).limit(limit)).scalars())


class DecisionRepository:
    def create(self, **payload) -> InvestmentDecision:
        with session_scope() as session:
            decision = InvestmentDecision(**payload)
            session.add(decision)
            session.flush()
            session.refresh(decision)
            return decision

    def get(self, decision_id: str) -> InvestmentDecision | None:
        with session_scope() as session:
            return session.get(InvestmentDecision, decision_id)

    def update(self, decision_id: str, **payload) -> InvestmentDecision:
        with session_scope() as session:
            decision = session.get(InvestmentDecision, decision_id)
            if decision is None:
                raise FileNotFoundError(decision_id)
            for key, value in payload.items():
                setattr(decision, key, value)
            session.add(decision)
            session.flush()
            session.refresh(decision)
            return decision

    def attach_agent_run(self, decision_id: str, agent_run_id: str, supervisor_version: str = "v1") -> InvestmentDecision:
        """Attach orchestration provenance after either tool or fallback save."""
        return self.update(decision_id, agent_run_id=agent_run_id, supervisor_version=supervisor_version)

    def list_decisions_for_skill(self, skill_slug: str, limit: int = 200) -> list[InvestmentDecision]:
        with session_scope() as session:
            return list(session.execute(select(InvestmentDecision).where(InvestmentDecision.skill_slug == skill_slug).order_by(InvestmentDecision.created_at.desc()).limit(limit)).scalars())

    def list_skill_evidence(self, skill_slug: str, limit: int = 200) -> list[dict]:
        """Return decision/outcome/review evidence in one read-only snapshot."""
        with session_scope() as session:
            decisions = list(session.execute(select(InvestmentDecision).where(InvestmentDecision.skill_slug == skill_slug).order_by(InvestmentDecision.created_at.desc()).limit(limit)).scalars())
            rows = []
            for decision in decisions:
                outcome = session.execute(select(InvestmentDecisionOutcome).where(InvestmentDecisionOutcome.decision_id == decision.id).order_by(InvestmentDecisionOutcome.evaluation_date.desc(), InvestmentDecisionOutcome.id.desc())).scalars().first()
                review = session.execute(select(LegacyDecisionReview).where(LegacyDecisionReview.decision_id == decision.id).order_by(LegacyDecisionReview.created_at.desc(), LegacyDecisionReview.id.desc())).scalars().first()
                rows.append({"decision_id": decision.id, "tool_trace": list(decision.tool_trace or []), "market_regime": decision.market_regime, "candidates": list(decision.candidates or []), "portfolio_advice": dict(decision.portfolio_advice or {}), "outcome": outcome, "review": review})
            return rows

    def add_outcome(self, **payload) -> InvestmentDecisionOutcome:
        with session_scope() as session:
            outcome = InvestmentDecisionOutcome(**payload)
            session.add(outcome)
            session.flush()
            session.refresh(outcome)
            return outcome

    def list_decision_outcome_rows(self) -> list[dict]:
        """只读：联表 investment_decision × investment_decision_outcome，展开为纯 dict 行。

        供历史校准（engines/regime/calibration.py）使用；仅返回存在
        market_excess_return 且 decision 带有 market_regime 的行。
        """
        with session_scope() as session:
            pairs = session.execute(
                select(InvestmentDecision, InvestmentDecisionOutcome)
                .join(InvestmentDecisionOutcome, InvestmentDecisionOutcome.decision_id == InvestmentDecision.id)
                .where(InvestmentDecision.market_regime.is_not(None))
                .where(InvestmentDecisionOutcome.market_excess_return.is_not(None))
            ).all()
            return [
                {
                    "decision_id": decision.id,
                    "market_regime": decision.market_regime,
                    "skill_slug": decision.skill_slug,
                    "thesis": dict(decision.thesis or {}),
                    "tool_trace": list(decision.tool_trace or []),
                    "themes": list(decision.themes or []),
                    "horizon_days": outcome.horizon_days,
                    "market_excess_return": outcome.market_excess_return,
                    "evaluation_date": outcome.evaluation_date.isoformat() if outcome.evaluation_date else None,
                }
                for decision, outcome in pairs
            ]

    def get_outcome(self, decision_id: str, horizon_days: int | None = None) -> InvestmentDecisionOutcome | None:
        with session_scope() as session:
            query = select(InvestmentDecisionOutcome).where(InvestmentDecisionOutcome.decision_id == decision_id)
            if horizon_days is not None:
                query = query.where(InvestmentDecisionOutcome.horizon_days == horizon_days)
            return session.execute(query.order_by(InvestmentDecisionOutcome.evaluation_date.desc())).scalars().first()

    def list_outcomes_for_decisions(self, decision_ids: list[str]) -> list[InvestmentDecisionOutcome]:
        if not decision_ids:
            return []
        with session_scope() as session:
            return list(session.execute(select(InvestmentDecisionOutcome).where(InvestmentDecisionOutcome.decision_id.in_(decision_ids)).order_by(InvestmentDecisionOutcome.evaluation_date.asc(), InvestmentDecisionOutcome.id.asc())).scalars())

    def get_outcome_by_id(self, outcome_id: int) -> InvestmentDecisionOutcome | None:
        with session_scope() as session:
            return session.get(InvestmentDecisionOutcome, outcome_id)

    def add_review(self, **payload) -> LegacyDecisionReview:
        with session_scope() as session:
            review = LegacyDecisionReview(**payload)
            session.add(review)
            session.flush()
            session.refresh(review)
            return review

    def update_review(self, review_id: int, **payload) -> LegacyDecisionReview:
        with session_scope() as session:
            review = session.get(LegacyDecisionReview, review_id)
            if review is None:
                raise FileNotFoundError(review_id)
            for key, value in payload.items():
                setattr(review, key, value)
            session.add(review)
            session.flush()
            session.refresh(review)
            return review


class DecisionSnapshotRepository:
    """DecisionSnapshot 持久化（设计文档 §26/§82）：决策可重放的版本锚点。"""

    def save(self, **payload) -> DecisionSnapshot:
        with session_scope() as session:
            snapshot = DecisionSnapshot(**payload)
            session.add(snapshot)
            session.flush()
            session.refresh(snapshot)
            return snapshot

    @staticmethod
    def _ensure_decision_bundle_binding(session, *, decision_id: str, bundle_id: str, created_at=None) -> None:
        """Create or validate the immutable decision↔bundle database anchor."""
        binding = session.get(DecisionBundleBindingRecord, decision_id)
        by_bundle = session.execute(
            select(DecisionBundleBindingRecord).where(DecisionBundleBindingRecord.bundle_id == bundle_id)
        ).scalars().first()
        if binding is not None and binding.bundle_id != bundle_id:
            raise ValueError("decision/bundle binding conflict")
        if by_bundle is not None and by_bundle.decision_id != decision_id:
            raise ValueError("bundle/decision binding conflict")
        if binding is None and by_bundle is None:
            session.add(DecisionBundleBindingRecord(
                decision_id=decision_id,
                bundle_id=bundle_id,
                created_at=created_at or datetime.now(UTC),
            ))
            session.flush()

    def get(self, snapshot_id: str) -> DecisionSnapshot | None:
        with session_scope() as session:
            return session.get(DecisionSnapshot, snapshot_id)

    def get_for_decision(self, decision_id: str) -> DecisionSnapshot | None:
        with session_scope() as session:
            return session.execute(
                select(DecisionSnapshot)
                .where(DecisionSnapshot.decision_id == decision_id)
                .order_by(DecisionSnapshot.created_at.desc())
            ).scalars().first()

    def save_v3(self, snapshot: DecisionSnapshotV3 | dict) -> DecisionSnapshotV3Record:
        """Persist v3 append-only with idempotent same-payload semantics."""
        # ``BaseModel.model_copy`` may bypass validators; revalidate at the
        # persistence boundary before accepting an immutable record.
        item = DecisionSnapshotV3.model_validate(snapshot.model_dump(mode="python") if isinstance(snapshot, DecisionSnapshotV3) else snapshot)
        payload = item.model_dump(mode="json")
        with session_scope() as session:
            if session.get(DecisionInputBundleRecord, item.input_bundle.bundle_id) is None:
                raise ValueError("cannot persist decision snapshot without its input bundle")
            if session.get(InvestmentDecision, item.decision_id) is None:
                raise ValueError("cannot persist decision snapshot without its decision")
            self._ensure_decision_bundle_binding(
                session,
                decision_id=item.decision_id,
                bundle_id=item.input_bundle.bundle_id,
                created_at=item.decision_time,
            )
            existing = session.get(DecisionSnapshotV3Record, item.snapshot_id)
            if existing is not None:
                if existing.decision_id != item.decision_id or existing.bundle_id != item.input_bundle.bundle_id:
                    raise ValueError("decision snapshot v3 lineage conflict")
                if existing.snapshot_hash != item.snapshot_hash or existing.payload_json != payload:
                    raise ValueError("immutable decision snapshot v3 conflict")
                return existing
            by_hash = session.execute(select(DecisionSnapshotV3Record).where(DecisionSnapshotV3Record.snapshot_hash == item.snapshot_hash)).scalars().first()
            if by_hash is not None:
                if by_hash.payload_json != payload:
                    raise ValueError("decision snapshot v3 hash collision")
                return by_hash
            by_decision = session.execute(select(DecisionSnapshotV3Record).where(DecisionSnapshotV3Record.decision_id == item.decision_id)).scalars().first()
            if by_decision is not None:
                if by_decision.bundle_id != item.input_bundle.bundle_id:
                    raise ValueError("decision snapshot v3 decision/bundle lineage conflict")
                raise ValueError("immutable decision snapshot v3 conflict: decision already has a snapshot")
            by_bundle = session.execute(select(DecisionSnapshotV3Record).where(DecisionSnapshotV3Record.bundle_id == item.input_bundle.bundle_id)).scalars().first()
            if by_bundle is not None and by_bundle.decision_id != item.decision_id:
                raise ValueError("decision snapshot v3 bundle/decision lineage conflict")
            # Formal segment rows are immutable anchors too.  A snapshot may
            # only reuse rows already attached to this exact decision/bundle.
            for model in (SpecialistArtifactRecord, InvestmentProposalV2Record, PolicyEvaluationRecord):
                segment = session.execute(
                    select(model).where(or_(model.decision_id == item.decision_id, model.bundle_id == item.input_bundle.bundle_id))
                ).scalars().first()
                if segment is not None and (segment.decision_id != item.decision_id or segment.bundle_id != item.input_bundle.bundle_id):
                    raise ValueError("decision snapshot v3 formal segment lineage conflict")
            row = DecisionSnapshotV3Record(
                snapshot_id=item.snapshot_id,
                decision_id=item.decision_id,
                schema_version=item.schema_version,
                bundle_id=item.input_bundle.bundle_id,
                snapshot_hash=item.snapshot_hash,
                payload_json=payload,
                created_at=item.decision_time,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return row

    def get_v3(self, snapshot_id: str) -> DecisionSnapshotV3 | None:
        with session_scope() as session:
            row = session.get(DecisionSnapshotV3Record, snapshot_id)
            return DecisionSnapshotV3.model_validate(row.payload_json) if row is not None else None

    def get_v3_for_decision(self, decision_id: str) -> DecisionSnapshotV3 | None:
        with session_scope() as session:
            row = session.execute(
                select(DecisionSnapshotV3Record)
                .where(DecisionSnapshotV3Record.decision_id == decision_id)
                .order_by(DecisionSnapshotV3Record.created_at.desc())
            ).scalars().first()
            return DecisionSnapshotV3.model_validate(row.payload_json) if row is not None else None

    def save_formal_segments(self, *, decision_id: str, bundle_id: str, artifacts: list[Any], proposal: InvestmentProposalV2, policy: PolicyEvaluation) -> None:
        """Persist immutable specialist/proposal/policy anchors before snapshot."""
        with session_scope() as session:
            if session.get(DecisionInputBundleRecord, bundle_id) is None:
                raise ValueError("formal segments require an input bundle")
            if session.get(InvestmentDecision, decision_id) is None:
                raise ValueError("formal segments require a decision")
            self._ensure_decision_bundle_binding(session, decision_id=decision_id, bundle_id=bundle_id)
            # Bind both directions, including rows with different IDs.  This
            # prevents a new segment ID from smuggling an existing decision or
            # bundle into another formal chain.
            snapshot = session.execute(
                select(DecisionSnapshotV3Record).where(
                    or_(DecisionSnapshotV3Record.decision_id == decision_id, DecisionSnapshotV3Record.bundle_id == bundle_id)
                )
            ).scalars().first()
            if snapshot is not None and (snapshot.decision_id != decision_id or snapshot.bundle_id != bundle_id):
                raise ValueError("formal segments conflict with snapshot lineage")
            for model in (SpecialistArtifactRecord, InvestmentProposalV2Record, PolicyEvaluationRecord):
                segment = session.execute(
                    select(model).where(or_(model.decision_id == decision_id, model.bundle_id == bundle_id))
                ).scalars().first()
                if segment is not None and (segment.decision_id != decision_id or segment.bundle_id != bundle_id):
                    raise ValueError("formal segments decision/bundle lineage conflict")
            for artifact in artifacts:
                payload = artifact.model_dump(mode="json", exclude={"artifact_hash"})
                existing = session.get(SpecialistArtifactRecord, artifact.artifact_id)
                if existing is not None:
                    if existing.decision_id != decision_id or existing.bundle_id != bundle_id:
                        raise ValueError("specialist artifact lineage conflict")
                    if existing.artifact_hash != artifact.artifact_hash or existing.payload_json != payload:
                        raise ValueError("immutable specialist artifact conflict")
                else:
                    session.add(SpecialistArtifactRecord(artifact_id=artifact.artifact_id, decision_id=decision_id, bundle_id=bundle_id, artifact_hash=artifact.artifact_hash, payload_json=payload, created_at=artifact.model_dump(mode="json").get("created_at") or datetime.now(UTC)))
            proposal_payload = proposal.model_dump(mode="json", exclude={"proposal_hash"})
            existing = session.get(InvestmentProposalV2Record, proposal.proposal_id)
            if existing is not None:
                if existing.decision_id != decision_id or existing.bundle_id != bundle_id:
                    raise ValueError("proposal lineage conflict")
                if existing.proposal_hash != proposal.proposal_hash or existing.payload_json != proposal_payload:
                    raise ValueError("immutable proposal conflict")
            if existing is None:
                session.add(InvestmentProposalV2Record(proposal_id=proposal.proposal_id, decision_id=decision_id, bundle_id=bundle_id, proposal_hash=proposal.proposal_hash, payload_json=proposal_payload, created_at=datetime.now(UTC)))
            policy_payload = policy.model_dump(mode="json", exclude={"result_hash"})
            existing = session.get(PolicyEvaluationRecord, policy.policy_result_id)
            if existing is not None:
                if existing.decision_id != decision_id or existing.bundle_id != bundle_id:
                    raise ValueError("policy lineage conflict")
                if existing.result_hash != policy.result_hash or existing.payload_json != policy_payload:
                    raise ValueError("immutable policy evaluation conflict")
            if existing is None:
                session.add(PolicyEvaluationRecord(policy_result_id=policy.policy_result_id, decision_id=decision_id, bundle_id=bundle_id, result_hash=policy.result_hash, payload_json=policy_payload, created_at=datetime.now(UTC)))


class OutcomeRepository:
    """Immutable D7 outcome repository; facts must already be quant-owned."""

    def save(self, outcome: DecisionOutcome | dict) -> DecisionOutcomeRecord:
        item = DecisionOutcome.model_validate(outcome.model_dump(mode="python") if isinstance(outcome, DecisionOutcome) else outcome)
        payload = item.model_dump(mode="json")
        with session_scope() as session:
            if session.get(InvestmentDecision, item.decision_id) is None:
                raise ValueError("cannot save outcome for unknown decision")
            existing = session.get(DecisionOutcomeRecord, item.outcome_id) if item.outcome_id else None
            if existing is None and item.outcome_id:
                existing = session.execute(select(DecisionOutcomeRecord).where(DecisionOutcomeRecord.outcome_hash == item.outcome_hash)).scalars().first()
            if existing is not None:
                if existing.outcome_hash != item.outcome_hash or existing.payload_json != payload:
                    raise ValueError("immutable outcome conflict")
                return existing
            row = DecisionOutcomeRecord(outcome_id=item.outcome_id, decision_id=item.decision_id, outcome_hash=item.outcome_hash, payload_json=payload, created_at=item.measured_at)
            session.add(row)
            session.flush()
            session.refresh(row)
            return row

    def get(self, outcome_id: str) -> DecisionOutcome | None:
        with session_scope() as session:
            row = session.get(DecisionOutcomeRecord, outcome_id)
            return DecisionOutcome.model_validate(row.payload_json) if row is not None else None

    def list_for_decision(self, decision_id: str) -> list[DecisionOutcome]:
        with session_scope() as session:
            rows = session.execute(select(DecisionOutcomeRecord).where(DecisionOutcomeRecord.decision_id == decision_id).order_by(DecisionOutcomeRecord.created_at.asc())).scalars()
            return [DecisionOutcome.model_validate(row.payload_json) for row in rows]


class ReviewRepository:
    """Structured reviews stored separately from legacy review rows."""

    def save(self, review: DecisionReview | dict) -> DecisionReviewV2Record:
        item = DecisionReview.model_validate(review.model_dump(mode="python") if isinstance(review, DecisionReview) else review)
        payload = item.model_dump(mode="json")
        with session_scope() as session:
            if session.get(InvestmentDecision, item.decision_id) is None:
                raise ValueError("cannot save review for unknown decision")
            for outcome_id in item.outcome_refs:
                outcome_row = session.get(DecisionOutcomeRecord, outcome_id)
                if outcome_row is None:
                    raise ValueError(f"review references unknown outcome: {outcome_id}")
                if outcome_row.decision_id != item.decision_id:
                    raise ValueError(f"review outcome belongs to another decision: {outcome_id}")
            existing = session.get(DecisionReviewV2Record, item.review_id)
            if existing is None:
                existing = session.execute(select(DecisionReviewV2Record).where(DecisionReviewV2Record.review_hash == item.review_hash)).scalars().first()
            if existing is not None:
                if existing.review_hash != item.review_hash or existing.payload_json != payload:
                    raise ValueError("immutable review conflict")
                return existing
            row = DecisionReviewV2Record(review_id=item.review_id, decision_id=item.decision_id, review_hash=item.review_hash, payload_json=payload, created_at=item.created_at)
            session.add(row)
            session.flush()
            session.refresh(row)
            return row

    def get(self, review_id: str) -> DecisionReview | None:
        with session_scope() as session:
            row = session.get(DecisionReviewV2Record, review_id)
            return DecisionReview.model_validate(row.payload_json) if row is not None else None

    def get_for_decision(self, decision_id: str) -> DecisionReview | None:
        with session_scope() as session:
            row = session.execute(select(DecisionReviewV2Record).where(DecisionReviewV2Record.decision_id == decision_id).order_by(DecisionReviewV2Record.created_at.desc())).scalars().first()
            return DecisionReview.model_validate(row.payload_json) if row is not None else None


class DecisionMemoryRepository:
    """Independent decision-memory store with dedupe and anti-loop limits."""

    @staticmethod
    def build_from_candidate(candidate: Any, *, decision_id: str, review_id: str, created_at=None) -> DecisionMemory:
        from datetime import UTC, datetime
        from contracts.proposal import _hash
        timestamp = created_at or datetime.now(UTC)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("memory created_at must be timezone-aware")
        candidate_key = _hash({
            "review_id": review_id,
            "decision_id": decision_id,
            "memory_type": candidate.memory_type,
            "content": candidate.content,
            "scope": dict(candidate.scope),
            "source_decision_ids": list(candidate.source_decision_ids or [decision_id]),
        })
        return DecisionMemory.build(
            memory_id=f"memory-{candidate_key[:32]}",
            memory_type=candidate.memory_type, content=candidate.content,
            source_decision_ids=list(candidate.source_decision_ids or [decision_id]),
            provenance={"source_decision_id": decision_id, "review_id": review_id, "source_system": "stock_agent"},
            scope=dict(candidate.scope), confidence=candidate.confidence, created_at=timestamp,
            valid_from=candidate.valid_from, valid_to=candidate.valid_to,
        )

    def save(self, memory: DecisionMemory | dict) -> DecisionMemoryRecord:
        item = DecisionMemory.model_validate(memory.model_dump(mode="python") if isinstance(memory, DecisionMemory) else memory)
        if item.weight > 0.5:
            raise ValueError("decision memory weight exceeds cap")
        payload = item.model_dump(mode="json")
        with session_scope() as session:
            for decision_id in item.source_decision_ids:
                if session.get(InvestmentDecision, decision_id) is None:
                    raise ValueError(f"decision memory references unknown decision: {decision_id}")
            existing = session.execute(select(DecisionMemoryRecord).where(DecisionMemoryRecord.dedupe_key == item.dedupe_key)).scalars().first()
            if existing is not None:
                if existing.memory_hash != item.memory_hash or existing.payload_json != payload:
                    raise ValueError("decision memory dedupe conflict")
                return existing
            row = DecisionMemoryRecord(memory_id=item.memory_id, memory_type=item.memory_type, memory_hash=item.memory_hash, dedupe_key=item.dedupe_key, status=item.status, payload_json=payload, created_at=item.created_at)
            session.add(row)
            session.flush()
            session.refresh(row)
            return row

    def get(self, memory_id: str) -> DecisionMemory | None:
        with session_scope() as session:
            row = session.get(DecisionMemoryRecord, memory_id)
            return DecisionMemory.model_validate(row.payload_json) if row is not None else None

    def list_active(self, *, limit: int = 100) -> list[DecisionMemory]:
        with session_scope() as session:
            rows = session.execute(select(DecisionMemoryRecord).where(DecisionMemoryRecord.status == "ACTIVE").order_by(DecisionMemoryRecord.created_at.desc()).limit(limit)).scalars()
            return [DecisionMemory.model_validate(row.payload_json) for row in rows]
