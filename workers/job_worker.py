from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import contextmanager
from typing import Any

from storage.bootstrap import create_all
from storage.repositories.job_repository import JobTaskRepository


def process_one_job(worker_id: str | None = None, job_id: str | None = None) -> bool:
    repo = JobTaskRepository()
    worker = worker_id or f"job-worker-{socket.gethostname()}"
    task = repo.claim(job_id, worker) if job_id else repo.claim_next(worker, ["factor_mine"], lease_seconds=300)
    if task is None:
        return False
    try:
        if task["task_type"] == "factor_mine":
            from mcp_servers.factor_mining_server import mine_factors

            payload = task.get("payload") or {}
            with heartbeat_loop(repo, task["id"], worker):
                result = mine_factors(**payload)
            repo.mark_finished(task["id"], "SUCCEEDED", result_ref=json.dumps(result, ensure_ascii=False), error=None, worker_id=worker)
            return True
        raise ValueError(f"unsupported task_type: {task['task_type']}")
    except Exception as exc:  # noqa: BLE001
        repo.mark_failed(task["id"], {"code": type(exc).__name__, "message": str(exc)}, worker_id=worker)
        return True


def main() -> None:
    create_all()
    while True:
        if not process_one_job():
            time.sleep(2)


@contextmanager
def heartbeat_loop(repo: JobTaskRepository, task_id: str, worker_id: str, interval: int = 30):
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(interval):
            repo.heartbeat(task_id, worker_id)

    thread = threading.Thread(target=beat, name=f"heartbeat-{task_id}", daemon=True)
    repo.heartbeat(task_id, worker_id)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=interval)


if __name__ == "__main__":
    main()
