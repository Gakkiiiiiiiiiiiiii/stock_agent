"""Recovery-safe application service for frozen knowledge conclusions.

It deliberately accepts already validated bundles and never imports a content
client or model provider.  A future adapter may invoke those external seams
between ``begin_synthesis`` and ``seal_model_response``.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from app.application.knowledge_conclusion.deterministic_fallback import (
    fallback_conclusion,
)
from app.application.knowledge_conclusion.grounding import (
    citation_edges,
    ground_findings,
)
from app.domain.knowledge_conclusion import (
    Finding,
    KnowledgeConclusion,
    KnowledgeConclusionRequest,
)
from app.domain.knowledge_conclusion_run import (
    FrozenBundle,
    KnowledgeConclusionAuditMetadata,
    KnowledgeConclusionCitation,
    KnowledgeConclusionRun,
    KnowledgeConclusionRunState,
    KnowledgeConclusionStateConflict,
)
from app.ports.knowledge_conclusion_repository import (
    ABSENT_SEAL_DIGEST,
    KnowledgeConclusionRepository,
)


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def sealed_response_digest(run: KnowledgeConclusionRun) -> str | None:
    """Stable CAS identity for the opaque sealed response currently on a run."""
    return canonical_hash(run.sealed_model_response) if run.sealed_model_response is not None else None


def captured_seal_digest(run: KnowledgeConclusionRun) -> str:
    """A CAS value captured from one concrete durable run observation.

    The explicit absent-slot sentinel prevents a fallback from accidentally
    disabling the compare just because no provider response was persisted.
    """
    return sealed_response_digest(run) or ABSENT_SEAL_DIGEST


class ReplayMode(str):
    VERIFY_HASH = "VERIFY_HASH"
    RECOMPUTE_DETERMINISTIC = "RECOMPUTE_DETERMINISTIC"
    RECOMPUTE_MODEL = "RECOMPUTE_MODEL"


@dataclass(frozen=True)
class ModelReplayRequired:
    original_conclusion_id: str
    new_model_identity_required: bool = True


class KnowledgeConclusionRunService:
    def __init__(self, repository: KnowledgeConclusionRepository, *, clock: Callable[[], datetime] | None = None) -> None:
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(UTC))

    def reserve(self, *, request: KnowledgeConclusionRequest, idempotency_key: str,
                policy_version: str = "knowledge-conclusion.policy.v1",
                audit_metadata: KnowledgeConclusionAuditMetadata | None = None) -> KnowledgeConclusionRun:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must be nonblank")
        # Do not use the resolved clock form for idempotency.  In particular,
        # omitted and explicitly-null clocks are a caller-visible raw input
        # and must stay reproducible after effective clocks are frozen.
        raw_request = self._idempotency_request(request)
        raw_hash = canonical_hash({"request": raw_request, "conclusion_policy_version": policy_version})
        now = self.clock()
        effective = request.model_copy(update={
            "business_as_of": request.business_as_of or now,
            "knowledge_as_of": request.knowledge_as_of or now,
            "availability_as_of": request.availability_as_of or now,
        })
        effective_hash = canonical_hash({
            "request": self._idempotency_request(effective),
            "conclusion_policy_version": policy_version,
        })
        return self.repository.reserve(KnowledgeConclusionRun(
            conclusion_id=str(uuid4()), idempotency_key=idempotency_key,
            raw_request=raw_request, raw_request_hash=raw_hash,
            effective_request_hash=effective_hash, request=effective,
            policy_version=policy_version, audit_metadata=audit_metadata,
            created_at=now, updated_at=now,
        ))

    @staticmethod
    def _idempotency_request(request: KnowledgeConclusionRequest) -> dict[str, Any]:
        """Return the caller identity without breaking persisted v1 retries.

        The v1 contract was implicit before this field existed.  Keeping that
        default omitted makes a retry of an already frozen v1 run hash exactly
        as it did at creation, while a v2 selection is explicit and therefore
        cannot share an idempotency key with v1.  The fully typed request (with
        its v1 default) is persisted independently for recovery.
        """
        payload = request.model_dump(mode="json")
        if request.content_bundle_contract == "content-knowledge-bundle.v1":
            payload.pop("content_bundle_contract", None)
        return payload

    def request_bundle(self, conclusion_id: str) -> KnowledgeConclusionRun:
        run = self._run(conclusion_id)
        if run.state in {
            KnowledgeConclusionRunState.BUNDLE_REQUESTED,
            KnowledgeConclusionRunState.BUNDLE_FROZEN,
            KnowledgeConclusionRunState.SYNTHESIZING,
            KnowledgeConclusionRunState.VALIDATING,
            KnowledgeConclusionRunState.SUCCEEDED,
        }:
            return run
        return self._save_transition(run, KnowledgeConclusionRunState.BUNDLE_REQUESTED)

    def freeze_bundle(self, conclusion_id: str, bundle: FrozenBundle) -> KnowledgeConclusionRun:
        run = self._run(conclusion_id)
        if bundle.content_snapshot_id != run.request.content_snapshot_id:
            raise ValueError("BUNDLE_SNAPSHOT_MISMATCH")
        if run.frozen_bundle is not None:
            if run.frozen_bundle == bundle:
                return run
            raise ValueError("BUNDLE_ALREADY_FROZEN")
        staged = replace(run, frozen_bundle=bundle)
        return self._save_transition(staged, KnowledgeConclusionRunState.BUNDLE_FROZEN, expected=run.version)

    def begin_synthesis(self, conclusion_id: str, *, worker_id: str) -> KnowledgeConclusionRun:
        run = self._run(conclusion_id)
        if run.state in {
            KnowledgeConclusionRunState.SYNTHESIZING,
            KnowledgeConclusionRunState.VALIDATING,
            KnowledgeConclusionRunState.SUCCEEDED,
        }:
            return run
        if not worker_id.strip():
            raise ValueError("worker_id must be nonblank")
        staged = replace(run, model_request_id=f"kc-model:{run.conclusion_id}", model_provider_idempotency_key=f"kc-provider:{run.conclusion_id}")
        return self._save_transition(staged, KnowledgeConclusionRunState.SYNTHESIZING, expected=run.version)

    def seal_model_response(self, conclusion_id: str, *, sealed_response: dict[str, Any]) -> KnowledgeConclusionRun:
        run = self._run(conclusion_id)
        if run.state is KnowledgeConclusionRunState.VALIDATING and run.sealed_model_response == sealed_response:
            return run
        if run.state is not KnowledgeConclusionRunState.SYNTHESIZING:
            raise ValueError("MODEL_RESPONSE_NOT_EXPECTED")
        # This slot intentionally keeps structured response material, allowing an encryption/retention adapter.
        staged = replace(run, sealed_model_response=dict(sealed_response))
        return self._save_transition(
            staged,
            KnowledgeConclusionRunState.VALIDATING,
            expected=run.version,
            # Compare the slot observed before this method staged its first
            # seal, rather than the newly staged opaque value.
            expected_seal_digest=captured_seal_digest(run),
        )

    def seal_model_attempt(self, conclusion_id: str, *, sealed_attempt: dict[str, Any], expected_seal_digest: str | None = None) -> KnowledgeConclusionRun:
        """CAS-seal opaque model JSON while remaining able to make one repair.

        Validation is deliberately a separate transition: a crash after this
        save resumes from the same response and cannot reinterpret a new call.
        """
        run = self._run(conclusion_id)
        if run.state not in {KnowledgeConclusionRunState.SYNTHESIZING, KnowledgeConclusionRunState.VALIDATING}:
            raise ValueError("MODEL_ATTEMPT_NOT_EXPECTED")
        prior = dict(run.sealed_model_response or {})
        attempts = list(prior.get("attempts") or [])
        if len(attempts) >= 2:
            raise ValueError("MODEL_REPAIR_LIMIT_EXCEEDED")
        attempts.append(dict(sealed_attempt))
        staged = replace(run, sealed_model_response={"format": "knowledge-conclusion.sealed-response.v1", "attempts": attempts}, updated_at=self.clock(), version=run.version + 1)
        return self.repository.save(
            staged,
            expected_version=run.version,
            expected_seal_digest=expected_seal_digest or captured_seal_digest(run),
        )

    def begin_validation(self, conclusion_id: str, *, expected_seal_digest: str | None = None) -> KnowledgeConclusionRun:
        run = self._run(conclusion_id)
        if run.state is KnowledgeConclusionRunState.VALIDATING:
            if expected_seal_digest is not None and captured_seal_digest(run) != expected_seal_digest:
                raise KnowledgeConclusionStateConflict("SEALED_RESPONSE_CONCURRENT_REPLACEMENT")
            return run
        if run.state is not KnowledgeConclusionRunState.SYNTHESIZING:
            raise ValueError("VALIDATION_NOT_EXPECTED")
        return self._save_transition(
            run,
            KnowledgeConclusionRunState.VALIDATING,
            expected_seal_digest=expected_seal_digest or captured_seal_digest(run),
        )

    def commit_grounded(self, conclusion_id: str, *, result: KnowledgeConclusion, expected_seal_digest: str | None = None) -> KnowledgeConclusionRun:
        run = self._run(conclusion_id)
        if run.state is KnowledgeConclusionRunState.SUCCEEDED:
            return run
        if run.state is not KnowledgeConclusionRunState.VALIDATING or run.frozen_bundle is None:
            raise ValueError("GROUNDED_RESULT_NOT_EXPECTED")
        if result.conclusion_id != run.conclusion_id or result.content_bundle_id != run.frozen_bundle.bundle_id:
            raise ValueError("RESULT_FROZEN_BUNDLE_MISMATCH")
        ground_findings(run.frozen_bundle.payload, result.findings)
        citations = self._citations(run.frozen_bundle.payload, result)
        staged = replace(run, result=result, result_hash=canonical_hash(result.model_dump(mode="json")))
        staged = staged.transition(KnowledgeConclusionRunState.SUCCEEDED, now=self.clock())
        # Capture from this one read before preparing the atomic effect.  A
        # no-response fallback uses the explicit absent sentinel, not the
        # port's optional/no-compare value.
        expected = expected_seal_digest if expected_seal_digest is not None else captured_seal_digest(run)
        return self.repository.commit_result(staged, citations, expected_version=run.version, expected_seal_digest=expected)

    def replay(self, conclusion_id: str, *, mode: Literal["VERIFY_HASH", "RECOMPUTE_DETERMINISTIC", "RECOMPUTE_MODEL"]) -> dict[str, object] | ModelReplayRequired:
        run = self._run(conclusion_id)
        if run.frozen_bundle is None:
            raise ValueError("BUNDLE_NOT_FROZEN")
        if mode == ReplayMode.RECOMPUTE_MODEL:
            self.repository.audit(conclusion_id, mode, {"new_model_identity_required": True})
            return ModelReplayRequired(conclusion_id)
        if run.result is None or run.result_hash is None:
            raise ValueError("RESULT_NOT_AVAILABLE")
        if mode == ReplayMode.VERIFY_HASH:
            valid = run.result_hash == canonical_hash(run.result.model_dump(mode="json"))
            audit_id = self.repository.audit(conclusion_id, mode, {"valid": valid, "bundle_hash": run.frozen_bundle.bundle_hash})
            return {"audit_id": audit_id, "valid": valid, "result_hash": run.result_hash}
        if mode == ReplayMode.RECOMPUTE_DETERMINISTIC:
            if run.result.model.mode == "MODEL":
                # A committed model result is replayed from the response that
                # was durably sealed before grounding.  It is deliberately
                # not reinterpreted as a fallback result (and this path has no
                # model or Content port to call).
                try:
                    from app.application.knowledge_conclusion.synthesis import (
                        reconstruct_sealed_model_conclusion,
                    )

                    replayed = reconstruct_sealed_model_conclusion(run)
                    expected_citations = self._citations(run.frozen_bundle.payload, replayed)
                    persisted_citations = self.repository.citations(conclusion_id)
                    if (
                        len(expected_citations) != len(persisted_citations)
                        or set(expected_citations) != set(persisted_citations)
                    ):
                        raise ValueError("citation ownership mismatch")
                    result_hash = canonical_hash(replayed.model_dump(mode="json"))
                    if result_hash != run.result_hash:
                        raise ValueError("committed result hash mismatch")
                except ValueError:
                    # Never expose or persist opaque provider material in an
                    # audit record.  The stable code also ensures a missing
                    # legacy seal cannot be silently replayed as FALLBACK.
                    self.repository.audit(conclusion_id, mode, {
                        "valid": False,
                        "reason": "SEALED_MODEL_REPLAY_INVALID",
                    })
                    raise ValueError("DETERMINISTIC_MODEL_REPLAY_INVALID") from None
                audit_id = self.repository.audit(conclusion_id, mode, {
                    "valid": True,
                    "result_hash": result_hash,
                    "model_mode": "MODEL",
                })
                return {"audit_id": audit_id, "result": replayed, "result_hash": result_hash}
            findings = self._deterministic_replay_inputs(run)
            replayed = fallback_conclusion(
                request=run.request, bundle=run.frozen_bundle.payload, findings=findings,
                content_bundle_id=run.frozen_bundle.bundle_id, conclusion_id=run.conclusion_id, created_at=run.result.created_at,
            )
            result_hash = canonical_hash(replayed.model_dump(mode="json"))
            # A deterministic replay is a verification of the frozen fallback
            # policy and inputs, never an opportunity to reinterpret a
            # generated insufficiency template as new evidence.
            if result_hash != run.result_hash:
                raise ValueError("DETERMINISTIC_REPLAY_MISMATCH")
            audit_id = self.repository.audit(conclusion_id, mode, {"result_hash": result_hash})
            return {"audit_id": audit_id, "result": replayed, "result_hash": result_hash}
        raise ValueError("UNKNOWN_REPLAY_MODE")

    def _deterministic_replay_inputs(self, run: KnowledgeConclusionRun) -> tuple[Finding, ...]:
        """Recover the original fallback inputs recorded before result construction.

        Older rows recorded only the fallback reason.  Their insufficient
        template is recognisable from the frozen verdict and must be replayed
        with an empty grounded input rather than with the generated template.
        """
        for mode, detail in reversed(self.repository.audit_events(run.conclusion_id)):
            if mode != "MODEL_FALLBACK" or not isinstance(detail, dict):
                continue
            rows = detail.get("fallback_input_findings")
            if isinstance(rows, list):
                try:
                    return tuple(Finding.model_validate(row) for row in rows)
                except (TypeError, ValueError):
                    raise ValueError("DETERMINISTIC_REPLAY_INPUT_INVALID") from None
        if run.result is not None and run.result.verdict.value == "INSUFFICIENT_EVIDENCE":
            return ()
        return run.result.findings if run.result is not None else ()

    def _run(self, conclusion_id: str) -> KnowledgeConclusionRun:
        run = self.repository.get(conclusion_id)
        if run is None:
            raise KeyError(conclusion_id)
        return run

    def _save_transition(self, run: KnowledgeConclusionRun, target: KnowledgeConclusionRunState, *, expected: int | None = None, expected_seal_digest: str | None = None) -> KnowledgeConclusionRun:
        prior_version = run.version if expected is None else expected
        staged = run.transition(target, now=self.clock())
        return self.repository.save(
            staged,
            expected_version=prior_version,
            expected_seal_digest=expected_seal_digest or captured_seal_digest(run),
        )

    @staticmethod
    def _citations(bundle: dict[str, Any], result: KnowledgeConclusion) -> tuple[KnowledgeConclusionCitation, ...]:
        rows: list[KnowledgeConclusionCitation] = []
        for index, finding in enumerate(result.findings):
            rows.extend(
                KnowledgeConclusionCitation(
                    index,
                    edge.knowledge_id,
                    edge.evidence_id,
                    edge.quote_hash,
                    edge.quote_hash_provenance,
                )
                for edge in citation_edges(bundle, finding)
            )
        return tuple(rows)
