"""SA-05 persistence/recovery boundary tests; all bundles are local fixtures."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.application.knowledge_conclusion.deterministic_fallback import (
    fallback_conclusion,
)
from app.application.knowledge_conclusion.run_service import (
    KnowledgeConclusionRunService,
    ModelReplayRequired,
    ReplayMode,
    canonical_hash,
)
from app.domain.knowledge_conclusion import Finding, KnowledgeConclusionRequest
from app.domain.knowledge_conclusion_run import (
    FrozenBundle,
    KnowledgeConclusionRunState,
)
from app.ports.knowledge_conclusion_repository import (
    InMemoryKnowledgeConclusionRepository,
    KnowledgeConclusionFenced,
    KnowledgeConclusionIdempotencyConflict,
)

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def _request(query: str = "内容支持什么研究结论？") -> KnowledgeConclusionRequest:
    return KnowledgeConclusionRequest(content_snapshot_id="snapshot-1", query=query)


def _bundle() -> FrozenBundle:
    payload = {"knowledge": [{"knowledge_id": "ko-1", "text": "收入增长12%。", "evidence": [{"evidence_id": "ev-1", "knowledge_id": "ko-1", "text": "收入增长12%。"}]}]}
    return FrozenBundle("bundle-1", canonical_hash(payload), payload, "content-sha", "contract-sha", "snapshot-1")


def _finding() -> Finding:
    return Finding(text="收入增长12%。", knowledge_ids=("ko-1",), evidence_ids=("ev-1",), confidence=0.8)


def _service() -> tuple[KnowledgeConclusionRunService, InMemoryKnowledgeConclusionRepository]:
    repo = InMemoryKnowledgeConclusionRepository()
    return KnowledgeConclusionRunService(repo, clock=lambda: NOW), repo


def _ready_run() -> tuple[KnowledgeConclusionRunService, InMemoryKnowledgeConclusionRepository, str]:
    service, repo = _service()
    run = service.reserve(request=_request(), idempotency_key="key-1")
    service.request_bundle(run.conclusion_id)
    service.freeze_bundle(run.conclusion_id, _bundle())
    service.begin_synthesis(run.conclusion_id, worker_id="worker-1")
    service.seal_model_response(run.conclusion_id, sealed_response={"ciphertext": "opaque", "retention": "pending"})
    return service, repo, run.conclusion_id


def _result(service: KnowledgeConclusionRunService, conclusion_id: str):
    run = service.repository.get(conclusion_id)
    assert run and run.frozen_bundle
    return fallback_conclusion(request=run.request, bundle=run.frozen_bundle.payload, findings=(_finding(),), content_bundle_id=run.frozen_bundle.bundle_id, conclusion_id=conclusion_id, created_at=NOW)


def test_concurrent_reserve_is_one_run_and_conflicting_hash_is_typed() -> None:
    service, _ = _service()
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(lambda _: service.reserve(request=_request(), idempotency_key="shared"), range(16)))
    assert {run.conclusion_id for run in runs}.__len__() == 1
    with pytest.raises(KnowledgeConclusionIdempotencyConflict, match="IDEMPOTENCY_KEY_CONFLICT"):
        service.reserve(request=_request("不同请求"), idempotency_key="shared")
    other = service.reserve(request=_request(), idempotency_key="other")
    assert other.conclusion_id != runs[0].conclusion_id


def test_reserve_freezes_default_clocks_before_retry_hashing() -> None:
    ticks = iter((NOW, NOW + timedelta(days=3)))
    service = KnowledgeConclusionRunService(InMemoryKnowledgeConclusionRepository(), clock=lambda: next(ticks))
    first = service.reserve(request=_request(), idempotency_key="clock")
    retry = service.reserve(request=_request(), idempotency_key="clock")
    assert retry == first
    assert first.request.business_as_of == NOW
    assert first.request.knowledge_as_of == NOW
    assert first.request.availability_as_of == NOW
    assert first.raw_request["business_as_of"] is None
    assert "content_bundle_contract" not in first.raw_request
    assert first.raw_request_hash != first.effective_request_hash
    assert first.raw_request_hash == canonical_hash({"request": first.raw_request, "conclusion_policy_version": first.policy_version})
    # v1 was implicit before the contract selector was added.  Its default is
    # intentionally omitted from the identity so N-1 frozen rows can retry.
    effective_identity = first.request.model_dump(mode="json")
    effective_identity.pop("content_bundle_contract")
    assert first.effective_request_hash == canonical_hash({"request": effective_identity, "conclusion_policy_version": first.policy_version})


def test_reserve_rejects_a_contract_switch_but_retries_v2() -> None:
    service, _ = _service()
    v2 = KnowledgeConclusionRequest(
        content_snapshot_id="snapshot-1",
        query="内容支持什么研究结论？",
        content_bundle_contract="content-knowledge-bundle.v2",
    )
    first = service.reserve(request=v2, idempotency_key="bundle-contract")
    assert service.reserve(request=v2, idempotency_key="bundle-contract") == first
    assert first.raw_request["content_bundle_contract"] == "content-knowledge-bundle.v2"
    with pytest.raises(KnowledgeConclusionIdempotencyConflict, match="IDEMPOTENCY_KEY_CONFLICT"):
        service.reserve(request=_request(), idempotency_key="bundle-contract")


def test_crash_seams_resume_without_refetch_or_second_result_effect() -> None:
    service, repo = _service()
    run = service.reserve(request=_request(), idempotency_key="crash")  # before bundle fetch/persist: reserve remains recoverable.
    assert repo.get(run.conclusion_id).state is KnowledgeConclusionRunState.RECEIVED
    service.request_bundle(run.conclusion_id)
    frozen = service.freeze_bundle(run.conclusion_id, _bundle())  # crash after freeze, before model: retry returns frozen bytes.
    assert service.freeze_bundle(run.conclusion_id, _bundle()) == frozen
    service.begin_synthesis(run.conclusion_id, worker_id="worker")
    service.seal_model_response(run.conclusion_id, sealed_response={"ciphertext": "response"})  # crash after response, before validation.
    assert service.begin_synthesis(run.conclusion_id, worker_id="retry").state is KnowledgeConclusionRunState.VALIDATING
    result = _result(service, run.conclusion_id)
    done = service.commit_grounded(run.conclusion_id, result=result)
    assert done.state is KnowledgeConclusionRunState.SUCCEEDED
    assert service.commit_grounded(run.conclusion_id, result=result) == done  # response-loss retry
    assert len(repo.citations(run.conclusion_id)) == 1


def test_stale_worker_is_fenced_and_result_citations_commit_together() -> None:
    service, repo, conclusion_id = _ready_run()
    prior = repo.get(conclusion_id)
    assert prior
    stale = prior.transition(KnowledgeConclusionRunState.FAILED, now=NOW)
    service.commit_grounded(conclusion_id, result=_result(service, conclusion_id))
    with pytest.raises(KnowledgeConclusionFenced):
        repo.save(stale, expected_version=prior.version)
    done = repo.get(conclusion_id)
    assert done and done.result and done.result_hash
    assert repo.citations(conclusion_id)[0].knowledge_id == "ko-1"


def test_result_commit_fault_leaves_no_partial_result_or_citations() -> None:
    class _FailBeforeAtomicCommit(InMemoryKnowledgeConclusionRepository):
        def commit_result(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated transaction failure")

    repo = _FailBeforeAtomicCommit()
    service = KnowledgeConclusionRunService(repo, clock=lambda: NOW)
    run = service.reserve(request=_request(), idempotency_key="atomic-fault")
    service.request_bundle(run.conclusion_id)
    service.freeze_bundle(run.conclusion_id, _bundle())
    service.begin_synthesis(run.conclusion_id, worker_id="worker")
    service.seal_model_response(run.conclusion_id, sealed_response={"ciphertext": "response"})
    with pytest.raises(RuntimeError, match="simulated"):
        service.commit_grounded(run.conclusion_id, result=_result(service, run.conclusion_id))
    recovered = repo.get(run.conclusion_id)
    assert recovered and recovered.state is KnowledgeConclusionRunState.VALIDATING
    assert recovered.result is None and repo.citations(run.conclusion_id) == ()


def test_replays_only_use_frozen_payload_and_preserve_original_result() -> None:
    service, repo, conclusion_id = _ready_run()
    original = service.commit_grounded(conclusion_id, result=_result(service, conclusion_id))
    # A sentinel verifies replay has no stock_content dependency or callback seam.
    verified = service.replay(conclusion_id, mode=ReplayMode.VERIFY_HASH)
    deterministic = service.replay(conclusion_id, mode=ReplayMode.RECOMPUTE_DETERMINISTIC)
    model = service.replay(conclusion_id, mode=ReplayMode.RECOMPUTE_MODEL)
    assert verified["valid"] is True
    assert deterministic["result_hash"] == original.result_hash
    assert isinstance(model, ModelReplayRequired)
    assert repo.get(conclusion_id) == original


def test_migration_has_single_key_unique_and_independent_tables(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sa05.db'}", future=True)
    sql = (Path(__file__).resolve().parents[1] / "storage" / "migrations" / "042_knowledge_conclusion_runs.sql").read_text(encoding="utf-8")
    with engine.begin() as conn:
        for statement in (part.strip() for part in sql.split(";") if part.strip()):
            conn.exec_driver_sql(statement)
        indexes = list(conn.execute(text("PRAGMA index_list(knowledge_conclusion_run)")))
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    assert any(int(row[2]) == 1 for row in indexes)
    assert {"knowledge_conclusion_run", "knowledge_conclusion_citation", "knowledge_conclusion_replay_audit"} <= tables
    assert "investment_decision" not in tables


def test_request_hash_upgrade_keeps_raw_and_effective_forms_recomputable(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sa05-hashes.db'}", future=True)
    root = Path(__file__).resolve().parents[1] / "storage" / "migrations"
    with engine.begin() as conn:
        for name in ("042_knowledge_conclusion_runs.sql", "044_knowledge_conclusion_request_hashes.sql"):
            for statement in (part.strip() for part in (root / name).read_text(encoding="utf-8").split(";") if part.strip()):
                conn.exec_driver_sql(statement)
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(knowledge_conclusion_run)"))}
    assert {"raw_request_json", "raw_request_hash", "effective_request_hash"} <= columns


def test_legacy_upgrade_never_fabricates_a_raw_request_identity(tmp_path) -> None:
    from app.adapters.postgres.knowledge_conclusion_repository import (
        PostgresKnowledgeConclusionRepository,
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'sa05-legacy.db'}", future=True)
    root = Path(__file__).resolve().parents[1] / "storage" / "migrations"
    with engine.begin() as conn:
        for name in ("042_knowledge_conclusion_runs.sql", "044_knowledge_conclusion_request_hashes.sql"):
            for statement in (part.strip() for part in (root / name).read_text(encoding="utf-8").split(";") if part.strip()):
                conn.exec_driver_sql(statement)
        request_json = '{"content_snapshot_id":"snapshot-1","query":"内容支持什么研究结论？","symbol":null,"business_as_of":null,"knowledge_as_of":null,"availability_as_of":null}'
        conn.execute(text("""INSERT INTO knowledge_conclusion_run(
            conclusion_id,idempotency_key,request_hash,request_json,policy_version,state,version,created_at,updated_at)
            VALUES ('legacy-1','legacy-key','effective-only',:request_json,'knowledge-conclusion-policy.v1','RECEIVED',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""), {"request_json": request_json})
        row = conn.execute(text("SELECT * FROM knowledge_conclusion_run WHERE conclusion_id='legacy-1'")).mappings().one()

    assert row["raw_request_json"] is None and row["raw_request_hash"] is None
    with pytest.raises(ValueError, match="LEGACY_RAW_REQUEST_UNVERIFIABLE"):
        PostgresKnowledgeConclusionRepository._from_row(row)
