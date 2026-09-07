"""Model orchestration over an already frozen, contract-validated bundle.

This application service owns no content client.  It calls only an injected
structured-model port, seals each returned JSON value before interpreting it,
and falls back to the deterministic grounded result on every unusable output.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.application.knowledge_conclusion.deterministic_fallback import (
    fallback_conclusion,
)
from app.application.knowledge_conclusion.grounding import (
    GroundingError,
    bundle_graph,
    validate_research_semantics,
)
from app.application.knowledge_conclusion.prompt import SYSTEM_PROMPT, build_prompt
from app.application.knowledge_conclusion.run_service import (
    KnowledgeConclusionRunService,
    canonical_hash,
    captured_seal_digest,
)
from app.domain.content_fact_tokens import extract_hard_facts, mapping_entities
from app.domain.knowledge_conclusion import (
    ConclusionVerdict,
    Finding,
    KnowledgeConclusion,
    ModelIdentity,
)
from app.domain.knowledge_conclusion_run import (
    KnowledgeConclusionRun,
    KnowledgeConclusionRunState,
    KnowledgeConclusionStateConflict,
)
from app.ports.knowledge_conclusion_model import (
    KnowledgeConclusionStructuredModel,
    StructuredModelRequest,
    StructuredModelResponse,
    StructuredModelUnavailable,
)
from app.ports.knowledge_conclusion_repository import KnowledgeConclusionFenced


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelConclusionPayload(_FrozenModel):
    """The deliberately small, structured-only synthesis contract."""

    verdict: ConclusionVerdict
    market_stance: Literal["BULLISH", "BEARISH", "NEUTRAL", "UNCERTAIN"] = "UNCERTAIN"
    findings: tuple[Finding, ...] = Field(min_length=1)
    summary_index: int = 0
    condition_indices: tuple[int, ...] = ()
    risk_indices: tuple[int, ...] = ()
    contradiction_indices: tuple[int, ...] = ()
    limitation_indices: tuple[int, ...] = ()


class ModelOutputInvalid(ValueError):
    """Stable repair reason; raw provider text is never promoted to a result."""

    code = "MODEL_OUTPUT_INVALID"


class SealedResponseBindingInvalid(ModelOutputInvalid):
    """A durable model response belongs to a different immutable run.

    This is intentionally distinct from a malformed provider response.  A
    malformed response can conservatively use the frozen deterministic
    fallback; a response whose seal was copied or rebound must not produce any
    result effect at all.
    """

    code = "SEALED_RESPONSE_BINDING_INVALID"


class SealedResponseConcurrentReplacement(ModelOutputInvalid):
    """A seal changed after it was decoded but before its result CAS."""

    code = "SEALED_RESPONSE_CONCURRENT_REPLACEMENT"


@dataclass(frozen=True)
class _Attempt:
    kind: Literal["PRIMARY", "REPAIR"]
    raw: dict[str, Any]
    provider: str
    model: str
    provider_response_id: str


class KnowledgeConclusionSynthesisService:
    """Exactly-once result effect with at-most-primary-plus-one-repair intent."""

    def __init__(
        self,
        run_service: KnowledgeConclusionRunService,
        model: KnowledgeConclusionStructuredModel,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.run_service = run_service
        self.model = model
        self.clock = clock or (lambda: datetime.now(UTC))

    def conclude(self, conclusion_id: str, *, worker_id: str) -> KnowledgeConclusion:
        """Resume from durable state without fetching content or re-freezing input."""
        run = self.run_service.repository.get(conclusion_id)
        if run is None:
            raise KeyError(conclusion_id)
        if run.result is not None:
            return run.result
        if run.frozen_bundle is None:
            raise ValueError("BUNDLE_NOT_FROZEN")
        if run.state is KnowledgeConclusionRunState.BUNDLE_FROZEN:
            run = self.run_service.begin_synthesis(conclusion_id, worker_id=worker_id)
        if run.state is KnowledgeConclusionRunState.VALIDATING:
            # A durable response is a recovery input, not a fallback signal.
            # Revalidate its latest sealed attempt before considering the
            # exact same bounded repair/fallback path used in the first run.
            try:
                attempts = _unseal_attempts(run.sealed_model_response, run=run)
            except SealedResponseBindingInvalid:
                self._reject_sealed_binding(run)
            except ModelOutputInvalid:
                return self._fallback(run, reason="SEALED_RESPONSE_INVALID", expected_seal_digest=captured_seal_digest(run))
            if attempts:
                return self._resume_sealed(run, attempts)
            return self._fallback(run, reason="SEALED_RESPONSE_MISSING", expected_seal_digest=captured_seal_digest(run))
        if run.state is not KnowledgeConclusionRunState.SYNTHESIZING:
            raise ValueError("SYNTHESIS_NOT_EXPECTED")

        try:
            attempts = _unseal_attempts(run.sealed_model_response, run=run)
        except SealedResponseBindingInvalid:
            self._reject_sealed_binding(run)
        except ModelOutputInvalid:
            # A corrupted opaque record must never cause a fresh provider call
            # or be interpreted as a result. The frozen bundle still supports
            # the conservative deterministic conclusion.
            return self._fallback(run, reason="SEALED_RESPONSE_INVALID", expected_seal_digest=captured_seal_digest(run))
        if not attempts:
            response = self._call_primary(run)
            if response is None:
                return self._fallback(run, reason="MODEL_UNAVAILABLE", expected_seal_digest=captured_seal_digest(run))
            run = self.run_service.seal_model_attempt(conclusion_id, sealed_attempt=_seal_attempt("PRIMARY", response, run=run))
            attempts = _unseal_attempts(run.sealed_model_response, run=run)

        return self._resume_sealed(run, attempts)

    def _resume_sealed(self, run: KnowledgeConclusionRun, attempts: tuple[_Attempt, ...]) -> KnowledgeConclusion:
        """Validate an already sealed attempt without repeating a provider effect."""
        latest = attempts[-1]
        validated_digest = captured_seal_digest(run)
        fallback_digest = validated_digest
        try:
            return self._validate_and_commit(run, latest, expected_seal_digest=validated_digest)
        except SealedResponseConcurrentReplacement:
            self._reject_concurrent_replacement(run)
        except (ModelOutputInvalid, GroundingError):
            if len(attempts) == 1:
                # A repair is an external effect.  Re-read and bind its
                # primary seal immediately before invoking it, then require
                # that exact opaque value when persisting the repair.
                primary_digest = validated_digest
                try:
                    current, current_attempts = self._current_bound_attempts(run.conclusion_id, primary_digest)
                except SealedResponseConcurrentReplacement:
                    self._reject_concurrent_replacement(run)
                if len(current_attempts) != 1:
                    self._reject_concurrent_replacement(run)
                response = self._call_repair(current, original=current_attempts[0], reason=ModelOutputInvalid.code)
                if response is not None:
                    try:
                        run = self.run_service.seal_model_attempt(
                            current.conclusion_id,
                            sealed_attempt=_seal_attempt("REPAIR", response, run=current),
                            expected_seal_digest=primary_digest,
                        )
                    except KnowledgeConclusionFenced:
                        self._reject_concurrent_replacement(current)
                    try:
                        # Always decode the post-CAS persisted repair, never
                        # the response value which existed only in this worker.
                        repair_digest = captured_seal_digest(run)
                        fallback_digest = repair_digest
                        current, current_attempts = self._current_bound_attempts(run.conclusion_id, repair_digest)
                        return self._validate_and_commit(
                            current,
                            current_attempts[-1],
                            expected_seal_digest=repair_digest,
                        )
                    except SealedResponseBindingInvalid:
                        self._reject_sealed_binding(run)
                    except SealedResponseConcurrentReplacement:
                        self._reject_concurrent_replacement(run)
                    except (ModelOutputInvalid, GroundingError):
                        pass
            # If a repair was durably sealed, bind the fallback to that exact
            # repair digest.  Otherwise retain the primary digest validated
            # before the repair decision.
            return self._fallback(
                run,
                reason="MODEL_REPAIR_FAILED",
                expected_seal_digest=fallback_digest,
            )

    def _call_primary(self, run: KnowledgeConclusionRun) -> StructuredModelResponse | None:
        prompt = build_prompt(request=run.request, bundle_data=run.frozen_bundle.payload if run.frozen_bundle else {})
        return self._call(run, StructuredModelRequest(
            model_request_id=_required(run.model_request_id),
            provider_idempotency_key=_required(run.model_provider_idempotency_key),
            system_prompt=prompt.system, user_json=prompt.user_payload,
        ))

    def _call_repair(self, run: KnowledgeConclusionRun, *, original: _Attempt, reason: str) -> StructuredModelResponse | None:
        allowed_ids, hard_facts = _repair_constraints(run)
        payload = {
            "contract": "knowledge-conclusion.repair.v1",
            "original_model_json": original.raw,
            "reason_code": reason,
            "allowed_citation_ids": allowed_ids,
            "allowed_hard_facts": hard_facts,
            "tools": [],
        }
        return self._call(run, StructuredModelRequest(
            model_request_id=_required(run.model_request_id) + ":repair",
            provider_idempotency_key=_required(run.model_provider_idempotency_key) + ":repair",
            system_prompt=SYSTEM_PROMPT,
            user_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), repair=True,
        ))

    def _call(self, run: KnowledgeConclusionRun, request: StructuredModelRequest) -> StructuredModelResponse | None:
        try:
            return self.model.complete(request)
        except StructuredModelUnavailable:
            self.run_service.repository.audit(run.conclusion_id, "MODEL_FALLBACK", {"reason": "MODEL_UNAVAILABLE"})
            return None

    def _validate_and_commit(self, run: KnowledgeConclusionRun, attempt: _Attempt, *, expected_seal_digest: str) -> KnowledgeConclusion:
        expected_digest = expected_seal_digest
        try:
            # Transition first, then re-read the durable row.  A decoded
            # _Attempt is never used across a repository round trip.
            validating = self.run_service.begin_validation(run.conclusion_id, expected_seal_digest=expected_digest)
            current, attempts = self._current_bound_attempts(validating.conclusion_id, expected_digest)
            if not attempts:
                raise SealedResponseConcurrentReplacement(SealedResponseConcurrentReplacement.code)
            result = _model_conclusion_from_attempt(current, attempts[-1], created_at=self.clock())
        except (KnowledgeConclusionFenced, KnowledgeConclusionStateConflict) as exc:
            raise SealedResponseConcurrentReplacement(SealedResponseConcurrentReplacement.code) from exc
        except (ValidationError, ValueError, TypeError, IndexError) as exc:
            if isinstance(exc, SealedResponseConcurrentReplacement):
                raise
            if isinstance(exc, GroundingError):
                raise
            raise ModelOutputInvalid(ModelOutputInvalid.code) from exc
        try:
            return _required_result(self.run_service.commit_grounded(
                current.conclusion_id, result=result, expected_seal_digest=expected_digest,
            ).result)
        except KnowledgeConclusionFenced as exc:
            raise SealedResponseConcurrentReplacement(SealedResponseConcurrentReplacement.code) from exc

    def _current_bound_attempts(self, conclusion_id: str, expected_digest: str) -> tuple[KnowledgeConclusionRun, tuple[_Attempt, ...]]:
        current = self.run_service.repository.get(conclusion_id)
        if current is None or captured_seal_digest(current) != expected_digest:
            raise SealedResponseConcurrentReplacement(SealedResponseConcurrentReplacement.code)
        return current, _unseal_attempts(current.sealed_model_response, run=current)

    def _fallback(self, run: KnowledgeConclusionRun, *, reason: str, expected_seal_digest: str | None = None) -> KnowledgeConclusion:
        # This value is captured by the caller before the vulnerable decision
        # window.  Never replace it with a digest taken from the reloaded row.
        expected_digest = expected_seal_digest or captured_seal_digest(run)
        current = self.run_service.repository.get(run.conclusion_id) or run
        if captured_seal_digest(current) != expected_digest:
            self._reject_concurrent_replacement(run)
        if current.result is not None:
            return current.result
        if current.frozen_bundle is None:
            raise ValueError("BUNDLE_NOT_FROZEN")
        if current.state is KnowledgeConclusionRunState.SYNTHESIZING:
            try:
                current = self.run_service.begin_validation(
                    current.conclusion_id,
                    expected_seal_digest=expected_digest,
                )
            except (KnowledgeConclusionFenced, KnowledgeConclusionStateConflict) as exc:
                raise SealedResponseConcurrentReplacement(SealedResponseConcurrentReplacement.code) from exc
        if current.state is not KnowledgeConclusionRunState.VALIDATING:
            raise ValueError("FALLBACK_NOT_EXPECTED")
        result = fallback_conclusion(
            request=current.request, bundle=current.frozen_bundle.payload, findings=(),
            content_bundle_id=current.frozen_bundle.bundle_id, conclusion_id=current.conclusion_id, created_at=self.clock(),
        )
        self.run_service.repository.audit(
            current.conclusion_id,
            "MODEL_FALLBACK",
            {
                "reason": reason,
                "fallback_policy_version": "deterministic-grounding-v1",
                # The fallback currently receives no model finding.  Persist
                # that semantic input before the generated insufficiency
                # template is committed, so replay cannot promote it.
                "fallback_input_findings": [],
            },
        )
        try:
            return _required_result(self.run_service.commit_grounded(
                current.conclusion_id,
                result=result,
                expected_seal_digest=expected_digest,
            ).result)
        except KnowledgeConclusionFenced as exc:
            self._reject_concurrent_replacement(current)
            raise AssertionError("unreachable") from exc

    def _reject_sealed_binding(self, run: KnowledgeConclusionRun) -> None:
        """Audit an identity attack without transitioning or committing a run."""
        self.run_service.repository.audit(
            run.conclusion_id,
            "SEALED_RESPONSE_REJECTED",
            {"reason": SealedResponseBindingInvalid.code},
        )
        raise SealedResponseBindingInvalid(SealedResponseBindingInvalid.code)

    def _reject_concurrent_replacement(self, run: KnowledgeConclusionRun) -> None:
        self.run_service.repository.audit(
            run.conclusion_id,
            SealedResponseConcurrentReplacement.code,
            {"reason": SealedResponseConcurrentReplacement.code},
        )
        raise SealedResponseConcurrentReplacement(SealedResponseConcurrentReplacement.code)


def _seal_attempt(kind: Literal["PRIMARY", "REPAIR"], response: StructuredModelResponse, *, run: KnowledgeConclusionRun) -> dict[str, Any]:
    """Seal a response together with the immutable synthesis identity.

    The binding is not an authorization secret; it is an integrity assertion
    used to reject a response copied from another run during recovery/replay.
    """
    if run.frozen_bundle is None:
        raise ValueError("BUNDLE_NOT_FROZEN")
    raw = json.dumps(response.structured_json, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    model_request_id, provider_idempotency_key = _attempt_request_identity(run, kind)
    binding = {
        "conclusion_id": run.conclusion_id,
        "model_request_id": model_request_id,
        "provider_idempotency_key": provider_idempotency_key,
        "raw_request_hash": run.raw_request_hash,
        "effective_request_hash": run.effective_request_hash,
        "bundle_id": run.frozen_bundle.bundle_id,
        "bundle_hash": run.frozen_bundle.bundle_hash,
        "policy_version": run.policy_version,
        "prompt_version": "knowledge-conclusion.prompt.v1",
        "model_provider": response.provider,
        "model_name": response.model,
        "sealed_from_run_version": run.version,
    }
    return {
        "kind": kind, "encoding": "base64-json", "retention": "encryption-ready-opaque-v1",
        "opaque_payload": base64.b64encode(raw).decode("ascii"), "response_hash": canonical_hash(response.structured_json),
        "provider_response_id": response.provider_response_id, "provider": response.provider, "model": response.model,
        "binding": binding, "binding_hash": canonical_hash(binding),
    }


def _unseal_attempts(sealed: Mapping[str, Any] | None, *, run: KnowledgeConclusionRun) -> tuple[_Attempt, ...]:
    """Decode only seals that bind to the current durable run identity."""
    if not sealed:
        return ()
    if sealed.get("format") != "knowledge-conclusion.sealed-response.v1":
        raise ModelOutputInvalid("SEALED_RESPONSE_INVALID")
    rows = sealed.get("attempts")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return ()
    attempts: list[_Attempt] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ModelOutputInvalid("SEALED_RESPONSE_INVALID")
        try:
            raw = json.loads(base64.b64decode(str(row["opaque_payload"]), validate=True))
            if not isinstance(raw, dict) or canonical_hash(raw) != row["response_hash"]:
                raise ValueError("response hash mismatch")
            _validate_attempt_binding(row, run, ordinal=len(attempts))
            attempts.append(_Attempt(str(row["kind"]), raw, _required(row.get("provider")), _required(row.get("model")), _required(row.get("provider_response_id"))))
        except SealedResponseBindingInvalid:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ModelOutputInvalid("SEALED_RESPONSE_INVALID") from exc
    if [item.kind for item in attempts] not in ([], ["PRIMARY"], ["PRIMARY", "REPAIR"]):
        raise ModelOutputInvalid("SEALED_RESPONSE_INVALID")
    return tuple(attempts)


def reconstruct_sealed_model_conclusion(run: KnowledgeConclusionRun) -> KnowledgeConclusion:
    """Pure deterministic MODEL replay from the original sealed response.

    This function must stay free of provider and Content calls.  It rebuilds
    the same conclusion using frozen clocks, Bundle, policy and prompt
    identity, then lets the caller compare citations and result hash.
    """
    if run.result is None or run.result.model.mode != "MODEL" or run.frozen_bundle is None:
        raise ValueError("SEALED_MODEL_REPLAY_INVALID")
    attempts = _unseal_attempts(run.sealed_model_response, run=run)
    if not attempts:
        raise ValueError("SEALED_MODEL_REPLAY_INVALID")
    latest = attempts[-1]
    if (latest.provider, latest.model) != (run.result.model.provider, run.result.model.model):
        raise ValueError("SEALED_MODEL_REPLAY_INVALID")
    return _model_conclusion_from_attempt(run, latest, created_at=run.result.created_at)


def _validate_attempt_binding(row: Mapping[str, Any], run: KnowledgeConclusionRun, *, ordinal: int) -> None:
    if run.frozen_bundle is None:
        raise SealedResponseBindingInvalid("sealed response binding missing run")
    binding = row.get("binding")
    if not isinstance(binding, Mapping) or row.get("binding_hash") != canonical_hash(binding):
        raise SealedResponseBindingInvalid("sealed response binding invalid")
    try:
        kind = row["kind"]
        if kind not in {"PRIMARY", "REPAIR"}:
            raise ValueError("attempt kind invalid")
        model_request_id, provider_idempotency_key = _attempt_request_identity(run, kind)
    except (KeyError, TypeError, ValueError) as exc:
        raise SealedResponseBindingInvalid("sealed response binding invalid") from exc
    expected = {
        "conclusion_id": run.conclusion_id,
        "model_request_id": model_request_id,
        "provider_idempotency_key": provider_idempotency_key,
        "raw_request_hash": run.raw_request_hash,
        "effective_request_hash": run.effective_request_hash,
        "bundle_id": run.frozen_bundle.bundle_id,
        "bundle_hash": run.frozen_bundle.bundle_hash,
        "policy_version": run.policy_version,
        "prompt_version": "knowledge-conclusion.prompt.v1",
        "model_provider": row.get("provider"),
        "model_name": row.get("model"),
    }
    if any(binding.get(key) != value for key, value in expected.items()):
        raise SealedResponseBindingInvalid("sealed response identity mismatch")
    sealed_version = binding.get("sealed_from_run_version")
    if not isinstance(sealed_version, int) or isinstance(sealed_version, bool) or sealed_version < 0:
        raise SealedResponseBindingInvalid("sealed response version missing")
    # The latest seal save advances the version once.  A primary response may
    # then take the SYNTHESIZING->VALIDATING transition; a repair is appended
    # while already VALIDATING and therefore has no second transition.  Admit
    # only those crash/recovery seams, never a reordered chain.
    rows = (run.sealed_model_response or {}).get("attempts") or []
    if not isinstance(rows, Sequence) or not rows:
        raise SealedResponseBindingInvalid("sealed response version missing")
    latest = rows[-1]
    if not isinstance(latest, Mapping):
        raise SealedResponseBindingInvalid("sealed response version missing")
    latest_binding = latest.get("binding")
    latest_sealed_version = latest_binding.get("sealed_from_run_version") if isinstance(latest_binding, Mapping) else None
    if not isinstance(latest_sealed_version, int) or isinstance(latest_sealed_version, bool):
        raise SealedResponseBindingInvalid("sealed response version missing")
    latest_saved_version = latest_sealed_version + 1
    attempt_count = len(rows)
    allowed_versions = {
        KnowledgeConclusionRunState.SYNTHESIZING: {latest_saved_version} if attempt_count == 1 else set(),
        KnowledgeConclusionRunState.VALIDATING: {latest_saved_version, latest_saved_version + 1} if attempt_count == 1 else {latest_saved_version},
        KnowledgeConclusionRunState.SUCCEEDED: {latest_saved_version + 1, latest_saved_version + 2} if attempt_count == 1 else {latest_saved_version + 1},
    }
    if run.version not in allowed_versions.get(run.state, set()):
        raise SealedResponseBindingInvalid("sealed response version mismatch")


def _attempt_request_identity(run: KnowledgeConclusionRun, kind: Literal["PRIMARY", "REPAIR"] | object) -> tuple[str, str]:
    if kind not in {"PRIMARY", "REPAIR"}:
        raise ValueError("attempt kind invalid")
    suffix = ":repair" if kind == "REPAIR" else ""
    return _required(run.model_request_id) + suffix, _required(run.model_provider_idempotency_key) + suffix


def _model_conclusion_from_attempt(run: KnowledgeConclusionRun, attempt: _Attempt, *, created_at: datetime) -> KnowledgeConclusion:
    payload = ModelConclusionPayload.model_validate(attempt.raw)
    selected = _select(payload)
    result = KnowledgeConclusion.construct(
        request=run.request, conclusion_id=run.conclusion_id,
        content_bundle_id=_required(run.frozen_bundle.bundle_id if run.frozen_bundle else None),
        verdict=payload.verdict, market_stance=payload.market_stance,
        summary=selected["summary"], findings=payload.findings,
        conditions=selected["conditions"], risks=selected["risks"],
        contradictions=selected["contradictions"], limitations=selected["limitations"],
        model=ModelIdentity(mode="MODEL", provider=attempt.provider, model=attempt.model), created_at=created_at,
    )
    if run.frozen_bundle is None:
        raise ModelOutputInvalid("BUNDLE_NOT_FROZEN")
    validate_research_semantics(
        run.frozen_bundle.payload,
        result.findings,
        verdict=result.verdict.value,
        market_stance=result.market_stance,
    )
    return result


def _repair_constraints(run: KnowledgeConclusionRun) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...]]:
    if run.frozen_bundle is None:
        raise ValueError("BUNDLE_NOT_FROZEN")
    graph = bundle_graph(run.frozen_bundle.payload)
    knowledge = tuple(item.knowledge_id for item in graph)
    evidence = tuple(item.evidence_id for item in graph for item in item.evidence)
    facts = set()
    for item in graph:
        for record in (item.raw, *(evidence.raw for evidence in item.evidence)):
            facts.update(extract_hard_facts(str(record.get("text", "")), entities=mapping_entities(record)))
    return {"knowledge_ids": knowledge, "evidence_ids": evidence}, tuple(sorted(facts))


def _select(payload: ModelConclusionPayload) -> dict[str, tuple[Finding, ...] | Finding]:
    findings = payload.findings
    def choose(indices: Sequence[int]) -> tuple[Finding, ...]:
        if any(index < 0 or index >= len(findings) for index in indices):
            raise ModelOutputInvalid("MODEL_OUTPUT_INVALID")
        return tuple(findings[index] for index in indices)
    summary = choose((payload.summary_index,))[0]
    return {"summary": summary, "conditions": choose(payload.condition_indices), "risks": choose(payload.risk_indices), "contradictions": choose(payload.contradiction_indices), "limitations": choose(payload.limitation_indices)}


def _required(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("required value missing")
    return value


def _required_result(value: KnowledgeConclusion | None) -> KnowledgeConclusion:
    if value is None:
        raise RuntimeError("result commit returned no result")
    return value
