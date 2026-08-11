from __future__ import annotations

from datetime import UTC, datetime, timedelta

from engines.memory.evidence import evidence_weight, load_memory_config, weighted_confidence
from storage.repositories.vector_repository import MemoryRepository


MEMORY_TTL_DAYS = {"MARKET_REGIME": 30, "THEME": 90}


class MemoryLifecycleService:
    def __init__(self, repository: MemoryRepository | None = None) -> None:
        self.repository = repository or MemoryRepository()

    def expire_due(self, now: datetime | None = None, limit: int = 500) -> dict:
        now = now or datetime.now(UTC)
        expired: list[int] = []
        for record in self.repository.list_all()[:limit]:
            if str(record.status).upper() in {"SUPERSEDED", "EXPIRED", "REJECTED"}:
                continue
            valid_to = record.valid_to
            ttl = MEMORY_TTL_DAYS.get(str(record.memory_type).upper())
            due = valid_to or (record.last_seen_at + timedelta(days=ttl) if ttl and record.last_seen_at else None)
            if due and due.replace(tzinfo=UTC) <= now:
                self.repository.update(record.id, status="EXPIRED", valid_to=due)
                expired.append(record.id)
        return {"expired_memory_ids": expired, "count": len(expired)}

    def validate(self, memory_id: int) -> dict:
        record = self.repository.update(memory_id, status="VALIDATED")
        return {"memory_id": record.id, "status": record.status}

    def record_outcome_evidence(
        self,
        memory_id: int,
        excess_return: float | None = None,
        *,
        decision_id: str | None = None,
        regime: str | None = None,
        horizon_days: int | None = None,
        market_excess_return: float | None = None,
        sector_excess_return: float | None = None,
        decision_quality: float | None = None,
        applicability: float | None = None,
    ) -> dict:
        """Record one structured outcome event and recompute evidence-weighted confidence.

        Lifecycle v2: every call persists a ``memory_evidence`` row, then the
        memory's confidence is recomputed as a signed weighted aggregation over
        all its evidence (see engines/memory/evidence.py). Transitions:

            confidence >= validate_confidence and count >= min_evidence_count
                -> VALIDATED
            confidence <= revalidate_confidence and count >= min_evidence_count
                -> REVALIDATION_REQUIRED
            otherwise -> current status is kept

        The legacy positional form ``record_outcome_evidence(memory_id, excess_return)``
        still works; the value is treated as ``market_excess_return``. The legacy
        consecutive counters in metadata (``outcome_support_count`` /
        ``outcome_failure_count``, reset on opposite sign) are still maintained
        alongside the new ``weighted_confidence`` / ``evidence_count`` keys.
        """
        record = self.repository.get(memory_id)
        if record is None:
            raise FileNotFoundError(memory_id)
        if market_excess_return is None and excess_return is not None:
            market_excess_return = float(excess_return)
        config = load_memory_config()["lifecycle"]
        evidence = self.repository.add_evidence(
            memory_id,
            decision_id=decision_id,
            regime=regime,
            horizon_days=horizon_days,
            market_excess_return=market_excess_return,
            sector_excess_return=sector_excess_return,
            decision_quality=decision_quality,
            applicability=applicability,
            weight=evidence_weight(
                {
                    "market_excess_return": market_excess_return,
                    "decision_quality": decision_quality,
                    "applicability": applicability,
                    "created_at": None,
                },
                config,
            ),
        )
        events = self.repository.list_evidence(memory_id)
        confidence = weighted_confidence(events, config)
        evidence_count = len(events)
        metadata = dict(record.metadata_json or {})
        support_count = int(metadata.get("outcome_support_count", 0))
        failure_count = int(metadata.get("outcome_failure_count", 0))
        if market_excess_return is not None:
            # Legacy consecutive counters deliberately reset on an opposite outcome:
            # they track the current streak, not a lifetime total.
            if float(market_excess_return) >= 0:
                support_count, failure_count = support_count + 1, 0
            else:
                support_count, failure_count = 0, failure_count + 1
        metadata.update(
            {
                "outcome_support_count": support_count,
                "outcome_failure_count": failure_count,
                "last_outcome_excess_return": market_excess_return,
                "last_outcome_at": datetime.now(UTC).isoformat(),
                "weighted_confidence": confidence,
                "evidence_count": evidence_count,
                "last_evidence_id": evidence.id,
            }
        )
        status = record.status
        if evidence_count >= int(config["min_evidence_count"]):
            if confidence >= float(config["validate_confidence"]):
                status = "VALIDATED"
            elif confidence <= float(config["revalidate_confidence"]):
                status = "REVALIDATION_REQUIRED"
        saved = self.repository.update(memory_id, metadata_json=metadata, status=status)
        return {
            "memory_id": saved.id,
            "status": saved.status,
            "outcome_support_count": support_count,
            "outcome_failure_count": failure_count,
            "evidence_count": evidence_count,
            "weighted_confidence": confidence,
        }
