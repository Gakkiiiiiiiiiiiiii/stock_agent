"""Immutable persistence records for DecisionInputBundle evidence lineage."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from storage.db import Base


class DecisionInputBundleRecord(Base):
    __tablename__ = "decision_input_bundle"

    bundle_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(40))
    decision_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    task_type: Mapped[str] = mapped_column(String(128))
    objective: Mapped[str] = mapped_column(Text)
    subjects_json: Mapped[list] = mapped_column(JSON, default=list)
    bundle_hash: Mapped[str] = mapped_column(String(64), unique=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class EvidenceRefRecord(Base):
    __tablename__ = "evidence_ref"
    __table_args__ = (UniqueConstraint("bundle_id", "evidence_id", name="uq_evidence_ref_bundle_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("decision_input_bundle.bundle_id"), index=True)
    evidence_id: Mapped[str] = mapped_column(String(160), index=True)
    evidence_type: Mapped[str] = mapped_column(String(64))
    source_system: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str | None] = mapped_column(String(256))
    snapshot_id: Mapped[str | None] = mapped_column(String(128))
    contract_version: Mapped[str] = mapped_column(String(64))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quality_status: Mapped[str] = mapped_column(String(32))
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class DecisionInputBundlePatchRecord(Base):
    __tablename__ = "decision_input_bundle_patch"
    __table_args__ = (UniqueConstraint("bundle_id", "previous_hash", "new_hash", name="uq_bundle_patch_lineage"),)

    patch_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("decision_input_bundle.bundle_id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    previous_hash: Mapped[str] = mapped_column(String(64))
    new_hash: Mapped[str] = mapped_column(String(64))
    evidence_json: Mapped[list] = mapped_column(JSON, default=list)
