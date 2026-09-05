"""交易复盘（decision review）与决策落库路由（从 app/api.py 平移，路由契约不变）。"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.adapters.postgres.decision_repository import SessionDecisionRepository
from app.adapters.postgres.session_outbox import SessionDecisionOutbox
from app.application.decision.unit_of_work import DecisionUnitOfWork
from app.domain.decision.authority import FormalDecisionResponseV2
from app.domain.decision.execution_authorization import FormalDecisionFinalizer
from app.domain.decision.run import DecisionRunState
from app.domain.lineage import DecisionLineageV1
from app.model_gateway.metrics import global_metrics
from app.observability.metrics import metrics as observability_metrics
from app.ports.decision_repository import IdempotencyConflict
from contracts.outcome import DecisionOutcome
from contracts.replay import ReplayRequest
from contracts.review import DecisionReview
from engines.decision.replay import DecisionReplayService
from financial_agent.models import TradeReviewInput
from storage.bootstrap import create_all
from storage.db import SessionLocal, bind_session
from storage.repositories.decision_input_repository import DecisionInputBundleRepository
from storage.repositories.research_repository import (
    DecisionSnapshotRepository,
    OutcomeRepository,
    ReviewRepository,
)

router = APIRouter()


def _registered_contract_checksum(contract_name: str) -> str:
    """Resolve a checksum from the repository manifest, never a guessed value."""
    import yaml

    manifest_path = Path(__file__).resolve().parents[2] / "contracts" / "platform-manifest.yaml"
    document = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    item = (document.get("contracts") or {}).get(contract_name)
    checksum = item.get("checksum") if isinstance(item, dict) else None
    if not isinstance(checksum, str) or not checksum.startswith("sha256:"):
        raise ValueError(f"CONTRACT_CHECKSUM_NOT_REGISTERED:{contract_name}")
    return checksum


def _formal_gate() -> tuple[bool, list[str], dict[str, Any]]:
    """Return a request-local readiness decision and immutable snapshot."""
    import os
    if os.getenv("STOCK_AGENT_OFFLINE_MODE") == "1" and os.getenv("STOCK_AGENT_DETERMINISTIC_FIXTURE") != "1":
        snapshot = {"ready": False, "checked_at": datetime.now(UTC).isoformat(),
                    "policy_version": "formal-readiness.v1", "reason_codes": ["OFFLINE_MODE_NOT_FORMAL"], "components": {}}
        return False, ["OFFLINE_MODE_NOT_FORMAL"], snapshot
    from app.routers.readiness import _service
    result = _service().check()
    snapshot = result.as_dict()
    return result.ready, list(result.reason_codes), snapshot


class DecisionReplayRequest(BaseModel):
    """决策回放请求体（§27 + 详细修改方案 §6）。

    original/EXACT_REPLAY：固定落库输入验证确定性；current：当前算法对比；
    COUNTERFACTUAL_REPLAY：相同输入 + override（policy_version/model/strategy）反事实分析。
    """

    mode: Literal["original", "current", "multi_agent", "EXACT_REPLAY", "COUNTERFACTUAL_REPLAY"] = "original"
    override: dict | None = None


class DecisionReplayV2Request(BaseModel):
    """Strict v2 replay input; never falls back to the legacy adapter."""

    mode: Literal[
        "EXACT_REPLAY", "MODEL_REPLAY", "SKILL_REPLAY", "POLICY_REPLAY",
        "WORKFLOW_REPLAY", "COUNTERFACTUAL_REPLAY",
    ]
    overrides: dict[str, Any] = Field(default_factory=dict)
    snapshot_id: str | None = None


class DecisionCreateRequest(BaseModel):
    """决策落库请求体（收尾文档 §38/§39）：结构化决策 + DecisionSnapshot 版本锚点。"""

    query: str | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    market_regime: str | None = None
    themes: list[str] = Field(default_factory=list)
    sector: str | None = None
    market_features: dict[str, Any] = Field(default_factory=dict)
    decision_snapshot: dict[str, Any] = Field(default_factory=dict)
    decision_quality: str | None = None


class DecisionV2Request(BaseModel):
    task_type: str
    objective: str
    subjects: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    as_of: datetime
    portfolio_id: str = "default"
    # Formal writes are always idempotent.  There is no implicit/default key:
    # accepting one would make a retried request indistinguishable from a new
    # decision and could create duplicate authoritative outputs.
    idempotency_key: str | None = Field(default=None, min_length=1)

    @field_validator("as_of")
    @classmethod
    def aware_as_of(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        return value


class OutcomeRefreshRequest(BaseModel):
    horizon: str
    measured_at: datetime

    @field_validator("measured_at")
    @classmethod
    def aware_measured_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("measured_at must be timezone-aware")
        return value


class ReviewRefreshRequest(BaseModel):
    outcome_refs: list[str] = Field(min_length=1)


@router.post("/api/v1/decisions", include_in_schema=False)
def create_decision(request: DecisionCreateRequest) -> dict:
    """Retained route marker; formal/actionable persistence is v2 bundle-first."""
    raise HTTPException(status_code=410, detail="legacy decision creation is retired; use POST /api/v2/decisions")


def _formalize_frozen_result(*, result: dict[str, Any], request: DecisionV2Request, run: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Validate the public response and optional authorization before commit."""
    snapshot = DecisionSnapshotRepository().get_v3_for_decision(str(result.get("decision_id") or run.decision_id))
    nodes = {str(item.get("type")): str(item.get("id")) for item in (getattr(snapshot, "lineage", None) or []) if isinstance(item, dict)}
    supplied = result.get("lineage") if isinstance(result.get("lineage"), dict) else {}
    policy = getattr(snapshot, "policy", None) or {}
    runtime_segment = getattr(snapshot, "runtime", None) or {}
    lineage = {
        "market_snapshot_id": nodes.get("MARKET_SNAPSHOT") or supplied.get("market_snapshot_id"),
        # Only the factor-owned evidence adapter may provide this identity;
        # a generic specialist artifact is not a factor artifact.
        "factor_artifact_id": nodes.get("FACTOR_ARTIFACT") or supplied.get("factor_artifact_id"),
        "content_snapshot_id": nodes.get("CONTENT_SNAPSHOT") or supplied.get("content_snapshot_id"),
        "policy_version": supplied.get("policy_version") or policy.get("policy_version") or (result.get("decision") or {}).get("policy_version"),
        "producer_commit": os.getenv("AGENT_GIT_COMMIT") or supplied.get("producer_commit"),
    }
    if any(not value for value in lineage.values()):
        raise ValueError("FORMAL_LINEAGE_REQUIRED")
    decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
    action = str(decision.get("investment_action") or decision.get("action") or "HOLD").upper()
    status = "HOLD" if action in {"HOLD", "WATCH"} else ("REJECTED" if action in {"VETO", "REJECT", "REJECTED"} else "APPROVED")
    envelope_data = None
    eligible = False
    if status == "APPROVED":
        if policy.get("approved") is not True:
            result["execution_ineligible_reason"] = "GOVERNANCE_REQUIRED"
        else:
            max_notional = Decimal(str(decision.get("max_notional") or decision.get("approved_notional") or decision.get("target_weight") or 0))
            trace_id = str(result.get("trace_id") or runtime_segment.get("trace_id") or (supplied.get("trace_id") or ""))
            bundle_id = str(result.get("bundle_id") or run.bundle_id or "")
            if not trace_id or not bundle_id:
                raise ValueError("FORMAL_LINEAGE_REQUIRED")
            try:
                envelope = FormalDecisionFinalizer().finalize(
                    decision={**decision, "state": "FINALIZED", "decision_id": str(result["decision_id"])},
                    snapshot_id=str(result.get("snapshot_id") or run.snapshot_id or ""),
                    governance={"approved": policy["approved"]}, policy_version=str(lineage["policy_version"]),
                    lineage={**lineage, "trace_id": trace_id, "request_id": run.request_id,
                             "decision_bundle_id": bundle_id, "decision_id": str(result["decision_id"]),
                             "decision_snapshot_id": str(result.get("snapshot_id") or run.snapshot_id or ""),
                             "portfolio_id": request.portfolio_id},
                    valid_until=decision.get("valid_until") or (request.as_of + timedelta(days=1)),
                    portfolio_id=request.portfolio_id,
                    contract_checksums={"formal-decision.v2": _registered_contract_checksum("formal-decision.v2")},
                    allowed_actions=(action,), max_notional=max_notional,
                    strategy_version=str(decision.get("strategy_version") or "route-finalizer"),
                )
            except (InvalidOperation, TypeError, ValueError) as exc:
                result["execution_ineligible_reason"] = str(exc)
            else:
                envelope_data = envelope.model_dump(mode="json")
                eligible = True
    trace_id = str(result.get("trace_id") or runtime_segment.get("trace_id") or supplied.get("trace_id") or "")
    bundle_id = str(result.get("bundle_id") or run.bundle_id or "")
    snapshot_value = result.get("snapshot_id") or run.snapshot_id
    if not snapshot_value:
        raise ValueError("FORMAL_LINEAGE_REQUIRED")
    snapshot_id = str(snapshot_value)
    if not trace_id or not bundle_id or not snapshot_id:
        raise ValueError("FORMAL_LINEAGE_REQUIRED")
    lineage_with_identity = {
        **lineage, "trace_id": trace_id, "request_id": run.request_id,
        "decision_bundle_id": bundle_id, "decision_id": str(result["decision_id"]),
        "decision_snapshot_id": snapshot_id, "portfolio_id": request.portfolio_id,
    }
    response_model = FormalDecisionResponseV2.model_validate({
        "contract": "formal-decision.v2", "authority": "FORMAL",
        "decision_id": str(result["decision_id"]), "decision_snapshot_id": snapshot_id,
        "decision_bundle_id": bundle_id, "portfolio_id": request.portfolio_id,
        "status": status, "execution_eligible": eligible,
        "valid_until": decision.get("valid_until") or (request.as_of + timedelta(days=1)),
        "lineage": lineage,
    })
    response = dict(result)
    response.update(response_model.model_dump(mode="json"))
    response["authorization_envelope"] = envelope_data
    return response, lineage_with_identity, envelope_data


@router.post("/api/v2/decisions")
def create_decision_v2(request: DecisionV2Request) -> dict:
    """Freeze once, calculate only from the persisted bundle, finalize once."""
    from app import dependencies

    if not request.idempotency_key:
        raise HTTPException(status_code=422, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    gate = _formal_gate()
    ready, reasons = gate[0], gate[1]
    readiness_snapshot = gate[2] if len(gate) > 2 else {}
    if not ready:
        observability_metrics.inc("formal_readiness", value=0, status="blocked")
        raise HTTPException(status_code=503, detail={"code": "FORMAL_DECISION_NOT_READY", "reason_codes": reasons})
    runtime = getattr(dependencies.orchestrator, "runtime", dependencies.orchestrator)
    create_all()
    request_hash = hashlib.sha256(json.dumps(request.model_dump(mode="json", exclude={"idempotency_key"}), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    try:
        with SessionLocal() as session, bind_session(session):
            repository = SessionDecisionRepository(session)
            uow = DecisionUnitOfWork(repository, SessionDecisionOutbox(session))
            run = uow.receive(portfolio_id=request.portfolio_id, idempotency_key=request.idempotency_key, request_hash=request_hash)
            if run.state is DecisionRunState.RECEIVED:
                freeze = getattr(runtime, "freeze_bundle", None)
                if freeze is None:
                    raise ValueError("FORMAL_FREEZE_BUNDLE_REQUIRED")
                bundle = freeze(task_type=request.task_type, objective=request.objective, subjects=request.subjects,
                                context={**request.context, "portfolio_id": request.portfolio_id}, as_of=request.as_of,
                                decision_id=run.decision_id)
                # The route owns the freeze transaction even when an injected
                # runtime only assembles the immutable bundle.
                DecisionInputBundleRepository().save(bundle)
                bundle_id = str(bundle.get("bundle_id") if isinstance(bundle, dict) else bundle.bundle_id)
                bundle_hash = str(bundle.get("bundle_hash") if isinstance(bundle, dict) else bundle.bundle_hash)
                snapshot_value = ((bundle.get("query_context") or {}).get("trace_context", {}).get("snapshot_id")
                                  if isinstance(bundle, dict) else (bundle.query_context or {}).get("trace_context", {}).get("snapshot_id"))
                if not snapshot_value:
                    raise ValueError("DECISION_SNAPSHOT_REQUIRED")
                snapshot_id = str(snapshot_value)
                uow.freeze_bundle(bundle, bundle_id=bundle_id, bundle_hash=bundle_hash,
                                  readiness_snapshot={**readiness_snapshot, "ready": ready, "reason_codes": reasons})
                run = uow.run
                run.snapshot_id = snapshot_id or run.snapshot_id
                if run.snapshot_id:
                    repository.save(run, expected_version=run.version)
                session.commit()
            else:
                session.rollback()
                run = repository.get(run.decision_id)
                if run is None:
                    raise ValueError("DECISION_RUN_NOT_FOUND")
            if run.state is DecisionRunState.FINALIZED and run.final_response_json:
                return run.final_response_json

        with SessionLocal() as session, bind_session(session):
            repository = SessionDecisionRepository(session)
            repository.lock(run.decision_id)
            run = repository.get(run.decision_id)
            if run is None or not run.bundle_id:
                raise ValueError("FROZEN_BUNDLE_REQUIRED")
            if run.state is DecisionRunState.FINALIZED and run.final_response_json:
                return run.final_response_json
            bundle = DecisionInputBundleRepository().get_bundle(run.bundle_id)
            if bundle is None or bundle.bundle_hash != run.bundle_hash:
                raise ValueError("FROZEN_BUNDLE_HASH_MISMATCH")
            calculate = getattr(runtime, "decide_from_frozen_bundle", None)
            if calculate is None:
                raise ValueError("FORMAL_FROZEN_CALCULATOR_REQUIRED")
            result = calculate(bundle=bundle, task_type=request.task_type, objective=request.objective,
                               subjects=request.subjects, context={**request.context, "portfolio_id": request.portfolio_id},
                               decision_id=run.decision_id, snapshot_id=run.snapshot_id)
            if os.getenv("STOCK_AGENT_DETERMINISTIC_FIXTURE") == "1" or os.getenv("STOCK_AGENT_OFFLINE_MODE") == "1":
                # Fixture/offline capabilities are explicitly non-executable.
                result["decision"] = {**(result.get("decision") or {}), "investment_action": "HOLD", "action": "HOLD"}
            result["decision_id"] = run.decision_id
            result["bundle_id"] = run.bundle_id
            result["snapshot_id"] = result.get("snapshot_id") or run.snapshot_id
            uow = DecisionUnitOfWork(repository, SessionDecisionOutbox(session), run=run)
            if run.state is DecisionRunState.BUNDLE_FROZEN:
                uow.advance(DecisionRunState.SPECIALISTS_COMPLETED)
            if uow.run.state is DecisionRunState.SPECIALISTS_COMPLETED:
                uow.advance(DecisionRunState.FORMAL_CALCULATED, result_hash=hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest())
            if uow.run.state is DecisionRunState.FORMAL_CALCULATED:
                uow.advance(DecisionRunState.GOVERNED, result_hash=hashlib.sha256(json.dumps(result.get("decision", {}), sort_keys=True, default=str).encode()).hexdigest())
            response, lineage, authorization = _formalize_frozen_result(result=result, request=request, run=uow.run)
            from app import dependencies as app_dependencies
            # Persist the validated identifier-only DAG in the same SQL
            # transaction as final response, authorization and outbox.
            app_dependencies.lineage_graph.record(
                DecisionLineageV1.model_validate(lineage), session=session,
            )
            uow.finalize(final_result=response, snapshot_id=str(response["decision_snapshot_id"]), lineage=lineage,
                         authorization=authorization,
                         event_payload={"decision_id": run.decision_id, "snapshot_id": response["decision_snapshot_id"], "request_id": run.request_id})
            session.commit()
            return response
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/v1/review/trade", include_in_schema=False)
def review_trade(request: TradeReviewInput) -> dict:
    raise HTTPException(status_code=410, detail="legacy trade review is retired; use D7 decision review")


@router.get("/api/v1/decisions/{decision_id}/snapshot")
def get_decision_snapshot(decision_id: str) -> dict:
    """P0 X-03：返回完整 DecisionSnapshot v2（schema/runtime/proposal/policy/output/lineage）。"""
    snapshot = DecisionSnapshotRepository().get_for_decision(decision_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="decision snapshot not found")
    return {
        "snapshot_id": snapshot.snapshot_id,
        "decision_id": snapshot.decision_id,
        "schema_version": snapshot.schema_version,
        "market": dict(snapshot.market or {}),
        "content": dict(snapshot.content or {}),
        "factor": dict(snapshot.factor or {}),
        "strategy": dict(snapshot.strategy or {}),
        "agent": dict(snapshot.agent or {}),
        "model": dict(snapshot.model or {}),
        "runtime": dict(snapshot.runtime or {}),
        "tools": dict(snapshot.tools or {}),
        "inputs": dict(snapshot.inputs or {}),
        "proposal": dict(snapshot.proposal or {}),
        "policy": dict(snapshot.policy or {}),
        "output": dict(snapshot.output or {}),
        "portfolio": dict(snapshot.portfolio or {}),
        "risk": dict(snapshot.risk or {}),
        "lineage": list(snapshot.lineage or []),
        "decision_quality": snapshot.decision_quality,
    }


@router.get("/api/v2/decisions/{decision_id}/snapshot")
def get_decision_snapshot_v2(decision_id: str) -> dict:
    snapshot = DecisionSnapshotRepository().get_v3_for_decision(decision_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="decision snapshot not found")
    return snapshot.model_dump(mode="json")


@router.post("/api/v2/decisions/{decision_id}/outcomes/refresh")
def refresh_decision_outcome_v2(decision_id: str, request: OutcomeRefreshRequest) -> dict:
    """Fetch/validate an outcome through the durable worker lease service."""
    from app import dependencies
    from app.application.outcomes.service import OutcomeEvaluationService

    runtime = getattr(dependencies.orchestrator, "runtime", dependencies.orchestrator)
    provider = getattr(runtime, "outcome_provider", None)
    if provider is None:
        from engines.decision.outcome_service import OutcomeService
        provider = OutcomeService()
    try:
        raw = OutcomeEvaluationService(provider=provider).refresh(
            decision_id=decision_id,
            decision_snapshot_id=decision_id,
            horizon=request.horizon,
            measured_at=request.measured_at,
            owner_id=f"api:{os.getpid()}",
        )
        if isinstance(raw, DecisionOutcome):
            return raw.model_dump(mode="json")
        if not isinstance(raw, dict):
            raise ValueError("quant outcome provider returned a non-object")  # noqa: TRY004 - stable provider error code
        outcome = DecisionOutcome.build(**{**raw, "decision_id": decision_id, "horizon": request.horizon, "measured_at": request.measured_at, "source_system": "quant"})
        row = OutcomeRepository().save(outcome)
    except (ConnectionError, TimeoutError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="quant outcome dependency unavailable") from exc
    except ValueError as exc:
        code = str(exc)
        if code in {"DECISION_NOT_FOUND", "DECISION_SNAPSHOT_NOT_FOUND", "OUTCOME_NOT_FOUND"}:
            raise HTTPException(status_code=404, detail=code) from exc
        raise HTTPException(status_code=422, detail=code) from exc
    return outcome.model_dump(mode="json") | {"outcome_id": row.outcome_id}


@router.get("/api/v2/decisions/{decision_id}/outcomes")
def list_decision_outcomes_v2(decision_id: str) -> dict:
    return {"items": [item.model_dump(mode="json") for item in OutcomeRepository().list_for_decision(decision_id)]}


@router.post("/api/v2/decisions/{decision_id}/review")
def save_decision_review_v2(decision_id: str, request: ReviewRefreshRequest) -> dict:
    """Run the injected structured review pipeline over stored outcome refs."""
    from app import dependencies

    runtime = getattr(dependencies.orchestrator, "runtime", dependencies.orchestrator)
    provider = getattr(runtime, "review_provider", None)
    if provider is None:
        from engines.decision.review_service import ReviewService
        provider = ReviewService()
    try:
        build_review = getattr(provider, "save_review", None) or getattr(provider, "build_review", None) or getattr(provider, "review", None)
        if build_review is None:
            raise ValueError("review provider lacks structured build_review")
        raw = build_review(decision_id=decision_id, outcome_refs=list(request.outcome_refs))
        if isinstance(raw, DecisionReview):
            return raw.model_dump(mode="json")
        if not isinstance(raw, dict):
            raise ValueError("review provider returned a non-object")  # noqa: TRY004 - stable provider error code
        review = DecisionReview.build(**{**raw, "decision_id": decision_id, "outcome_refs": list(request.outcome_refs)})
        row = ReviewRepository().save(review)
    except ValueError as exc:
        code = str(exc)
        if code in {"DECISION_NOT_FOUND", "DECISION_SNAPSHOT_NOT_FOUND", "OUTCOME_NOT_FOUND"}:
            raise HTTPException(status_code=404, detail=code) from exc
        raise HTTPException(status_code=422, detail=code) from exc
    return review.model_dump(mode="json") | {"review_id": row.review_id}


@router.get("/api/v2/decisions/{decision_id}/review")
def get_decision_review_v2(decision_id: str) -> dict:
    review = ReviewRepository().get_for_decision(decision_id)
    if review is None:
        raise HTTPException(status_code=404, detail="decision review not found")
    return review.model_dump(mode="json")


@router.post("/api/v1/decision/{decision_id}/replay")
@router.post("/api/v1/decisions/{decision_id}/replay")  # §27 规范路径别名
def replay_decision(decision_id: str, request: DecisionReplayRequest | None = None) -> dict:
    """决策回放（§27）：重放确定性决策链并与落库产物比对，决策不存在返回 404。"""
    mode = request.mode if request is not None else "original"
    overrides = request.override if request is not None else None
    result = DecisionReplayService().replay(decision_id, mode=mode, overrides=overrides)
    if result.get("error") == "DECISION_NOT_FOUND":
        raise HTTPException(status_code=404, detail="decision not found")
    return result


@router.post("/api/v2/decisions/{decision_id}/replay")
def replay_decision_v2(decision_id: str, request: DecisionReplayV2Request) -> dict:
    """Replay only an immutable v3 snapshot through the v2 engine entrypoint."""
    from app import dependencies

    try:
        replay_service = getattr(dependencies.orchestrator, "replay_service", None)
        if replay_service is None:
            runtime = getattr(dependencies.orchestrator, "runtime", None)
            replay_service = getattr(runtime, "replay_service", None) if runtime is not None else None
        replay_service = replay_service or DecisionReplayService()
        replay_request = ReplayRequest(
            decision_id=decision_id,
            mode=request.mode,
            overrides=request.overrides,
            snapshot_id=request.snapshot_id,
        )
        result = replay_service.replay_v2(replay_request)
        if request.mode == "EXACT_REPLAY" and isinstance(result, dict) and not result.get("error"):
            from app.application.replay.run_service import ReplayRunService
            snapshot = DecisionSnapshotRepository().get_v3_for_decision(decision_id)
            if snapshot is not None:
                snapshot_payload = snapshot.model_dump(mode="json")
                # The v2 EXACT path only accepts a persisted snapshot; its
                # calculator is fixed inside ReplayRunService and cannot be
                # replaced by a request-supplied callable.
                ReplayRunService(persistent=True).run_exact_fixed(
                    decision_snapshot_id=str(snapshot.snapshot_id), snapshot=snapshot_payload,
                    expected_output_hash=None,
                )
        global_metrics().increment(
            "replay_total",
            mode=request.mode,
            status=(result.get("status") if isinstance(result, dict) else None) or ("REJECTED" if isinstance(result, dict) and result.get("error") else "SUCCEEDED"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    error = result.get("error") if isinstance(result, dict) else None
    if error == "REPLAY_SNAPSHOT_REQUIRED":
        raise HTTPException(status_code=404, detail="decision snapshot v3 not found")
    if error == "DECISION_NOT_FOUND":
        raise HTTPException(status_code=404, detail="decision not found")
    return result
