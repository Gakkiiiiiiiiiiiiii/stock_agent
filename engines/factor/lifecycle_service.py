from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from engines.factor.lifecycle import FactorLifecycleStatus
from engines.factor.library import load_library, save_library
from financial_agent.utils import project_root


ALLOWED_TRANSITIONS = {
    FactorLifecycleStatus.DRAFT.value: {FactorLifecycleStatus.COMPUTABLE.value},
    FactorLifecycleStatus.COMPUTABLE.value: {FactorLifecycleStatus.IN_SAMPLE_PASS.value},
    FactorLifecycleStatus.IN_SAMPLE_PASS.value: {FactorLifecycleStatus.OOS_PASS.value},
    FactorLifecycleStatus.OOS_PASS.value: {FactorLifecycleStatus.PAPER_TRADING.value},
    FactorLifecycleStatus.PAPER_TRADING.value: {FactorLifecycleStatus.APPROVED.value},
    FactorLifecycleStatus.APPROVED.value: {FactorLifecycleStatus.ACTIVE.value},
    FactorLifecycleStatus.ACTIVE.value: {FactorLifecycleStatus.DEGRADED.value, FactorLifecycleStatus.RETIRED.value},
    FactorLifecycleStatus.DEGRADED.value: {FactorLifecycleStatus.RETIRED.value, FactorLifecycleStatus.ACTIVE.value},
}


class InvalidLifecycleTransition(ValueError):
    pass


class FactorLifecycleService:
    def __init__(self, library_path: str | Path | None = None, audit_path: str | Path | None = None) -> None:
        self.library_path = library_path
        self.audit_path = Path(audit_path) if audit_path else project_root() / "storage" / "runtime" / "factor_lifecycle_events.jsonl"

    def transition(
        self,
        factor_id: str,
        target_status: str,
        reason: str,
        actor: str,
        research_run_id: str | None = None,
    ) -> dict:
        target = FactorLifecycleStatus(target_status).value
        library = load_library(self.library_path)
        factor = next((item for item in library.get("factors", []) if item.get("id") == factor_id), None)
        if factor is None:
            raise KeyError(f"factor not found: {factor_id}")
        source = str(factor.get("status") or FactorLifecycleStatus.DRAFT.value)
        if target != FactorLifecycleStatus.LEGACY_UNVERIFIED.value and target not in ALLOWED_TRANSITIONS.get(source, set()):
            raise InvalidLifecycleTransition(f"invalid factor lifecycle transition: {source} -> {target}")
        factor["status"] = target
        factor["validation_stage"] = target
        factor["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save_library(library, self.library_path)
        event = {
            "factor_id": factor_id,
            "from_status": source,
            "to_status": target,
            "reason": reason,
            "actor": actor,
            "research_run_id": research_run_id,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return event
