"""job_task 表消费者（设计文档 §29 统一任务契约）。

task_type 常量集中在 workers/job_types.py；派发经 JOB_HANDLERS 表驱动。
Content 与 Factor 任务由远程子系统 Worker 负责；本 Worker 仅执行 Agent 所属任务。

幂等：入队侧由 JobTaskRepository.create(idempotency_key=...) 去重；执行侧
决策 outcome/review 与 retrieval 评测按各自的持久化边界处理，重复执行同一
payload 不产生额外副作用。
"""
from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from storage.bootstrap import create_all
from storage.repositories.job_repository import JobTaskRepository
from workers.job_types import JOB_TASK_TYPES, JobType


class LeaseLostError(RuntimeError):
    pass


def _handle_memory_lifecycle_sweep(payload: dict[str, Any], ensure_lease: Callable[[], None]) -> dict:
    from datetime import datetime

    from engines.memory.lifecycle import MemoryLifecycleService

    now = datetime.fromisoformat(payload["now"]) if payload.get("now") else None
    return MemoryLifecycleService().expire_due(now=now, limit=int(payload.get("limit") or 500))


def _handle_decision_outcome(payload: dict[str, Any], ensure_lease: Callable[[], None]) -> dict:
    from datetime import UTC, datetime, time

    from engines.decision.outcome_service import OutcomeService

    measured = payload.get("measured_at") or payload.get("evaluation_date")
    if isinstance(measured, str) and len(measured) == 10:
        measured = datetime.combine(datetime.fromisoformat(measured).date(), time(16), tzinfo=UTC)
    elif isinstance(measured, str):
        measured = datetime.fromisoformat(measured)
    if not isinstance(measured, datetime):
        raise ValueError("OUTCOME_MEASURED_AT_REQUIRED")  # noqa: TRY004 - public worker error code
    from app.application.outcomes.service import OutcomeEvaluationService
    worker = _outcome_worker()
    snapshot_id = str(payload.get("decision_snapshot_id") or payload["decision_id"])
    result = OutcomeEvaluationService(provider=OutcomeService(), worker=worker).refresh(
        decision_id=payload["decision_id"],
        decision_snapshot_id=snapshot_id,
        horizon=str(payload.get("horizon") or f"T+{payload.get('horizon_days', 1)}"),
        measured_at=measured,
        owner_id=socket.gethostname(),
    )
    if result is None:
        raise RuntimeError("OUTCOME_LEASE_BUSY")
    return result


_SHARED_OUTCOME_WORKER = None


def _outcome_worker():
    global _SHARED_OUTCOME_WORKER
    if _SHARED_OUTCOME_WORKER is None:
        from workers.outcome_worker import OutcomeWorker
        _SHARED_OUTCOME_WORKER = OutcomeWorker()
    return _SHARED_OUTCOME_WORKER


def _handle_decision_review(payload: dict[str, Any], ensure_lease: Callable[[], None]) -> dict:
    from engines.decision.review_service import ReviewService
    refs = list(payload.get("outcome_refs") or [])
    if not refs:
        raise ValueError("OUTCOME_REFS_REQUIRED")
    return ReviewService().save_review(decision_id=payload["decision_id"], outcome_refs=refs).model_dump(mode="json")


def _handle_retrieval_evaluation(payload: dict[str, Any], ensure_lease: Callable[[], None]) -> dict:
    """运行 fixture 语料检索评测并落盘报告（同输出目录覆盖写，幂等）。"""
    from engines.retrieval.evaluation.pipeline import run_fixture_evaluation

    kwargs: dict[str, Any] = {"ablation": bool(payload.get("ablation", False))}
    if payload.get("dataset"):
        kwargs["dataset"] = payload["dataset"]
    if payload.get("output_dir"):
        kwargs["output_dir"] = payload["output_dir"]
    return run_fixture_evaluation(**kwargs)


#: task_type → Agent-owned 处理器。
JOB_HANDLERS: dict[str, Callable[[dict[str, Any], Callable[[], None]], Any]] = {
    JobType.MEMORY_LIFECYCLE_SWEEP: _handle_memory_lifecycle_sweep,
    JobType.MEMORY_EXPIRE: _handle_memory_lifecycle_sweep,
    JobType.MEMORY_REVALIDATION: _handle_memory_lifecycle_sweep,
    JobType.DECISION_OUTCOME: _handle_decision_outcome,
    JobType.DECISION_REVIEW: _handle_decision_review,
    JobType.RETRIEVAL_EVALUATION: _handle_retrieval_evaluation,
}

assert set(JOB_HANDLERS) == set(JOB_TASK_TYPES), "job_worker 派发表必须与 JobType.JOB_TASK_TYPES 一致"


def process_one_job(worker_id: str | None = None, job_id: str | None = None) -> bool:
    repo = JobTaskRepository()
    worker = worker_id or f"job-worker-{socket.gethostname()}"
    task = repo.claim(job_id, worker) if job_id else repo.claim_next(worker, list(JOB_TASK_TYPES), lease_seconds=300)
    if task is None:
        return False
    lease_token = task.get("lease_token")
    lease_version = task.get("lease_version")
    try:
        handler = JOB_HANDLERS.get(task["task_type"])
        if handler is None:
            raise ValueError(f"unsupported task_type: {task['task_type']}")
        with heartbeat_loop(repo, task["id"], worker, lease_token=lease_token, lease_version=lease_version) as ensure_lease:
            result = handler(task.get("payload") or {}, ensure_lease)
            ensure_lease()
        repo.mark_finished(
            task["id"],
            "SUCCEEDED",
            result_ref=json.dumps(result, ensure_ascii=False, default=str),
            error=None,
            worker_id=worker,
            lease_token=lease_token,
            lease_version=lease_version,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        repo.mark_failed(
            task["id"],
            {"code": type(exc).__name__, "message": str(exc)},
            worker_id=worker,
            lease_token=lease_token,
            lease_version=lease_version,
        )
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Process Agent-owned durable jobs")
    parser.add_argument("--once", action="store_true", help="claim at most one job and exit")
    args = parser.parse_args()
    create_all()
    if args.once:
        process_one_job()
        return
    while True:
        if not process_one_job():
            time.sleep(2)


@contextmanager
def heartbeat_loop(
    repo: JobTaskRepository,
    task_id: str,
    worker_id: str,
    interval: int = 30,
    lease_token: str | None = None,
    lease_version: int | None = None,
):
    stop = threading.Event()
    lease_lost = threading.Event()

    def send_heartbeat() -> bool:
        try:
            result = repo.heartbeat(task_id, worker_id, lease_token=lease_token, lease_version=lease_version)
        except TypeError:
            result = repo.heartbeat(task_id, worker_id)
        return result is not False

    def ensure_lease() -> None:
        if lease_lost.is_set():
            lease_lost.set()
            raise LeaseLostError(f"job lease lost: task_id={task_id}")
        if lease_token is None or lease_version is None:
            return
        if not repo.has_lease(task_id, worker_id, lease_token, lease_version):
            lease_lost.set()
            raise LeaseLostError(f"job lease lost: task_id={task_id}")

    def beat() -> None:
        while not stop.wait(interval):
            if not send_heartbeat():
                lease_lost.set()
                return

    thread = threading.Thread(target=beat, name=f"heartbeat-{task_id}", daemon=True)
    if not send_heartbeat():
        lease_lost.set()
    thread.start()
    try:
        ensure_lease()
        yield ensure_lease
        ensure_lease()
    finally:
        stop.set()
        thread.join(timeout=interval)


if __name__ == "__main__":
    main()
