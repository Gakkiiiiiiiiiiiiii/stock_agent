from uuid import uuid4
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from storage.db import session_scope
from storage.repositories.job_repository import JobTaskRepository


def test_job_idempotency():
    repo = JobTaskRepository()
    JobTaskRepository._schema_ready = False
    task_type = f"test_job_{uuid4().hex}"
    key = f"same-{uuid4()}"
    first = repo.create(task_type, {"rounds": 1}, idempotency_key=key)
    second = repo.create(task_type, {"rounds": 1}, idempotency_key=key)
    assert first["id"] == second["id"]


def test_job_claim_and_retry():
    repo = JobTaskRepository()
    JobTaskRepository._schema_ready = False
    task_type = f"test_job_{uuid4().hex}"
    task = repo.create(task_type, {"rounds": 1, "test_id": str(uuid4())}, max_retries=1)
    claimed = repo.claim_next(f"worker-1-{uuid4()}", [task_type])
    assert claimed["id"] == task["id"]
    assert claimed["status"] == "RUNNING"
    repo.mark_failed(task["id"], {"message": "boom"})
    assert repo.get(task["id"])["status"] == "FAILED_RETRYABLE"
    claimed_again = repo.claim_next("worker-2", [task_type])
    assert claimed_again["id"] == task["id"]
    repo.mark_failed(task["id"], {"message": "boom again"})
    assert repo.get(task["id"])["status"] == "FAILED_FINAL"


def test_job_claim_respects_lease_timeout():
    repo = JobTaskRepository()
    JobTaskRepository._schema_ready = False
    task_type = f"test_job_{uuid4().hex}"
    task = repo.create(task_type, {"rounds": 1, "test_id": str(uuid4())}, max_retries=1)
    claimed = repo.claim_next("worker-1", [task_type], lease_seconds=60)
    assert claimed["id"] == task["id"]
    assert repo.claim_next("worker-2", [task_type], lease_seconds=60) is None
    stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=120)
    with session_scope() as session:
        session.execute(text("UPDATE job_task SET heartbeat_at=:heartbeat_at WHERE id=:id"), {"heartbeat_at": stale, "id": task["id"]})
    reclaimed = repo.claim_next("worker-2", [task_type], lease_seconds=60)
    assert reclaimed["id"] == task["id"]
    assert reclaimed["worker_id"] == "worker-2"


def test_stale_worker_cannot_heartbeat_or_finish_after_reclaim():
    repo = JobTaskRepository()
    JobTaskRepository._schema_ready = False
    task_type = f"test_job_{uuid4().hex}"
    task = repo.create(task_type, {"rounds": 1, "test_id": str(uuid4())}, max_retries=1)
    first_claim = repo.claim_next("worker-1", [task_type], lease_seconds=60)
    stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=120)
    with session_scope() as session:
        session.execute(text("UPDATE job_task SET heartbeat_at=:heartbeat_at WHERE id=:id"), {"heartbeat_at": stale, "id": task["id"]})
    second_claim = repo.claim_next("worker-2", [task_type], lease_seconds=60)
    assert second_claim["lease_token"] != first_claim["lease_token"]
    assert int(second_claim["lease_version"]) == int(first_claim["lease_version"]) + 1
    assert repo.heartbeat(task["id"], "worker-1") is False
    assert repo.mark_finished(task["id"], "SUCCEEDED", result_ref="{}", worker_id="worker-1") is False
    assert repo.heartbeat(
        task["id"],
        "worker-2",
        lease_token=first_claim["lease_token"],
        lease_version=first_claim["lease_version"],
    ) is False
    assert repo.mark_finished(
        task["id"],
        "SUCCEEDED",
        result_ref="{}",
        worker_id="worker-2",
        lease_token=first_claim["lease_token"],
        lease_version=first_claim["lease_version"],
    ) is False
    assert repo.heartbeat(
        task["id"],
        "worker-2",
        lease_token=second_claim["lease_token"],
        lease_version=second_claim["lease_version"],
    ) is True
    assert repo.get(task["id"])["worker_id"] == "worker-2"
