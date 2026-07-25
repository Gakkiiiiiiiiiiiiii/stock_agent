from uuid import uuid4

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
