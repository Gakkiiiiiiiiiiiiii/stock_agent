from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import contextmanager
from typing import Any

from storage.bootstrap import create_all
from storage.repositories.job_repository import JobTaskRepository


class LeaseLostError(RuntimeError):
    pass


def process_one_job(worker_id: str | None = None, job_id: str | None = None) -> bool:
    repo = JobTaskRepository()
    worker = worker_id or f"job-worker-{socket.gethostname()}"
    task = repo.claim(job_id, worker) if job_id else repo.claim_next(worker, ["factor_mine", "knowledge_lifecycle_sweep", "decision_outcome", "decision_review"], lease_seconds=300)
    if task is None:
        return False
    lease_token = task.get("lease_token")
    lease_version = task.get("lease_version")
    try:
        if task["task_type"] == "factor_mine":
            from mcp_servers.factor_mining_server import mine_factors

            payload = task.get("payload") or {}
            with heartbeat_loop(repo, task["id"], worker, lease_token=lease_token, lease_version=lease_version) as ensure_lease:
                result = mine_factors(**payload, lease_guard=ensure_lease)
                ensure_lease()
            repo.mark_finished(
                task["id"],
                "SUCCEEDED",
                result_ref=json.dumps(result, ensure_ascii=False),
                error=None,
                worker_id=worker,
                lease_token=lease_token,
                lease_version=lease_version,
            )
            return True
        if task["task_type"] == "knowledge_lifecycle_sweep":
            from datetime import datetime

            from engines.content.knowledge_lifecycle_service import KnowledgeLifecycleService

            payload = task.get("payload") or {}
            now = datetime.fromisoformat(payload["now"]) if payload.get("now") else None
            limit = int(payload.get("limit") or 500)
            with heartbeat_loop(repo, task["id"], worker, lease_token=lease_token, lease_version=lease_version) as ensure_lease:
                result = KnowledgeLifecycleService().expire_due_units(now=now, limit=limit)
                ensure_lease()
            repo.mark_finished(
                task["id"],
                "SUCCEEDED",
                result_ref=json.dumps(result, ensure_ascii=False),
                error=None,
                worker_id=worker,
                lease_token=lease_token,
                lease_version=lease_version,
            )
            return True
        if task["task_type"] == "decision_outcome":
            from datetime import date
            from engines.decision.decision_service import DecisionService
            from engines.decision.outcome_evaluator import DecisionOutcomeEvaluator

            payload = task.get("payload") or {}
            with heartbeat_loop(repo, task["id"], worker, lease_token=lease_token, lease_version=lease_version) as ensure_lease:
                service = DecisionService()
                decision_result = service.get_decision(payload["decision_id"])
                if not decision_result.get("found"):
                    raise ValueError("DECISION_NOT_FOUND")
                metrics = DecisionOutcomeEvaluator().evaluate(decision_result["decision"], date.fromisoformat(payload["evaluation_date"]))
                result = service.record_outcome(payload["decision_id"], date.fromisoformat(payload["evaluation_date"]), int(payload["horizon_days"]), **metrics)
                ensure_lease()
            repo.mark_finished(task["id"], "SUCCEEDED", result_ref=json.dumps(result, ensure_ascii=False), worker_id=worker, lease_token=lease_token, lease_version=lease_version)
            return True
        if task["task_type"] == "decision_review":
            from engines.decision.decision_service import DecisionService

            payload = task.get("payload") or {}
            with heartbeat_loop(repo, task["id"], worker, lease_token=lease_token, lease_version=lease_version) as ensure_lease:
                service = DecisionService()
                outcome = service.get_outcome(payload["decision_id"], int(payload.get("horizon_days", 5)))
                if not outcome.get("found"):
                    raise ValueError("OUTCOME_NOT_READY")
                excess = outcome["outcome"].get("excess_return")
                quality = max(0.0, min(1.0, 0.5 + float(excess or 0) * 5))
                lesson = "相对基准表现偏弱，下一次应降低同类信号权重。" if excess is not None and excess < 0 else "该决策相对基准表现有效，继续跟踪其适用市场环境。"
                result = service.review(payload["decision_id"], {"decision_quality": quality, "lessons": [lesson]}, outcome["outcome"]["id"])
                ensure_lease()
            repo.mark_finished(task["id"], "SUCCEEDED", result_ref=json.dumps(result, ensure_ascii=False), worker_id=worker, lease_token=lease_token, lease_version=lease_version)
            return True
        raise ValueError(f"unsupported task_type: {task['task_type']}")
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
