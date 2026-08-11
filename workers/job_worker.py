"""job_task 表消费者（设计文档 §29 统一任务契约）。

task_type 常量集中在 workers/job_types.py；派发经 JOB_HANDLERS 表驱动。
历史类型字符串（factor_mine / knowledge_lifecycle_sweep / memory_lifecycle_sweep）
持续被认领执行；memory_expire / memory_revalidation 复用 memory sweep 处理器。

幂等：入队侧由 JobTaskRepository.create(idempotency_key=...) 去重；执行侧
market/sector 快照按 (键, trade_date, feature_version) upsert、retrieval
评测按输出目录覆盖写，重复执行同一 payload 不产生额外副作用。
"""
from __future__ import annotations

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


def _handle_factor_mine(payload: dict[str, Any], ensure_lease: Callable[[], None]) -> dict:
    from mcp_servers.factor_mining_server import mine_factors

    return mine_factors(**payload, lease_guard=ensure_lease)


def _handle_knowledge_lifecycle_sweep(payload: dict[str, Any], ensure_lease: Callable[[], None]) -> dict:
    from datetime import datetime

    from engines.content.knowledge_lifecycle_service import KnowledgeLifecycleService

    now = datetime.fromisoformat(payload["now"]) if payload.get("now") else None
    limit = int(payload.get("limit") or 500)
    return KnowledgeLifecycleService().expire_due_units(now=now, limit=limit)


def _handle_memory_lifecycle_sweep(payload: dict[str, Any], ensure_lease: Callable[[], None]) -> dict:
    from datetime import datetime

    from engines.memory.lifecycle import MemoryLifecycleService

    now = datetime.fromisoformat(payload["now"]) if payload.get("now") else None
    return MemoryLifecycleService().expire_due(now=now, limit=int(payload.get("limit") or 500))


def _handle_decision_outcome(payload: dict[str, Any], ensure_lease: Callable[[], None]) -> dict:
    from datetime import date

    from engines.decision.decision_service import DecisionService
    from engines.decision.outcome_evaluator import DecisionOutcomeEvaluator

    service = DecisionService()
    decision_result = service.get_decision(payload["decision_id"])
    if not decision_result.get("found"):
        raise ValueError("DECISION_NOT_FOUND")
    metrics = DecisionOutcomeEvaluator().evaluate(decision_result["decision"], date.fromisoformat(payload["evaluation_date"]))
    return service.record_outcome(payload["decision_id"], date.fromisoformat(payload["evaluation_date"]), int(payload["horizon_days"]), **metrics)


def _handle_decision_review(payload: dict[str, Any], ensure_lease: Callable[[], None]) -> dict:
    from engines.decision.review_runner import DecisionReviewRunner

    return DecisionReviewRunner().run(payload["decision_id"], int(payload.get("horizon_days", 5)))


def _handle_market_feature_snapshot(payload: dict[str, Any], ensure_lease: Callable[[], None]) -> dict:
    """计算并持久化市场特征快照（按 trade_date/feature_version upsert，幂等）。"""
    from engines.market.feature_service import MarketFeatureService
    from storage.repositories.market_feature_repository import MarketFeatureRepository

    as_of = _parse_datetime(payload.get("as_of"))
    return MarketFeatureService(repository=MarketFeatureRepository()).get_market_features(as_of=as_of)


def _handle_sector_feature_snapshot(payload: dict[str, Any], ensure_lease: Callable[[], None]) -> list[dict]:
    """计算并持久化板块强度快照（read_cache=False 强制刷新，upsert 幂等）。"""
    from engines.market.feature_service import SectorFeatureService
    from storage.repositories.market_feature_repository import MarketFeatureRepository

    as_of = _parse_datetime(payload.get("as_of"))
    top_k = int(payload.get("top_k") or 20)
    return SectorFeatureService(repository=MarketFeatureRepository()).get_sector_strength(top_k=top_k, as_of=as_of, read_cache=False)


def _handle_retrieval_evaluation(payload: dict[str, Any], ensure_lease: Callable[[], None]) -> dict:
    """运行 fixture 语料检索评测并落盘报告（同输出目录覆盖写，幂等）。"""
    from engines.retrieval.evaluation.pipeline import run_fixture_evaluation

    kwargs: dict[str, Any] = {"ablation": bool(payload.get("ablation", False))}
    if payload.get("dataset"):
        kwargs["dataset"] = payload["dataset"]
    if payload.get("output_dir"):
        kwargs["output_dir"] = payload["output_dir"]
    return run_fixture_evaluation(**kwargs)


def _parse_datetime(value: Any) -> Any:
    if not value:
        return None
    if isinstance(value, str):
        from datetime import datetime

        return datetime.fromisoformat(value)
    return value


#: task_type → 处理器。历史类型与 §29 新类型在此统一登记。
JOB_HANDLERS: dict[str, Callable[[dict[str, Any], Callable[[], None]], Any]] = {
    JobType.FACTOR_MINE: _handle_factor_mine,
    JobType.KNOWLEDGE_LIFECYCLE_SWEEP: _handle_knowledge_lifecycle_sweep,
    JobType.MEMORY_LIFECYCLE_SWEEP: _handle_memory_lifecycle_sweep,
    JobType.MEMORY_EXPIRE: _handle_memory_lifecycle_sweep,
    JobType.MEMORY_REVALIDATION: _handle_memory_lifecycle_sweep,
    JobType.DECISION_OUTCOME: _handle_decision_outcome,
    JobType.DECISION_REVIEW: _handle_decision_review,
    JobType.MARKET_FEATURE_SNAPSHOT: _handle_market_feature_snapshot,
    JobType.SECTOR_FEATURE_SNAPSHOT: _handle_sector_feature_snapshot,
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
    create_all()
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
