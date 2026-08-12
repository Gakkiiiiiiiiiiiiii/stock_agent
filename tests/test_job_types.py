"""§29 统一 Worker 任务契约：JobType 常量完整性与 job_worker 派发兼容性。"""
from __future__ import annotations

import pytest

from storage.repositories.job_repository import JobTaskRepository
from workers import job_worker
from workers.job_types import ALL_JOB_TYPES, EXTERNAL_QUEUE_TYPES, JOB_TASK_TYPES, JobType


def test_job_type_constants_cover_design_doc_list():
    """§29 目标类型 + 历史兼容类型全部登记，且字符串值稳定（对应存量数据）。"""
    expected = {
        "VECTOR_INDEX": "vector_index",
        "DECISION_OUTCOME": "decision_outcome",
        "DECISION_REVIEW": "decision_review",
        "MEMORY_EXPIRE": "memory_expire",
        "MEMORY_REVALIDATION": "memory_revalidation",
        "MARKET_FEATURE_SNAPSHOT": "market_feature_snapshot",
        "SECTOR_FEATURE_SNAPSHOT": "sector_feature_snapshot",
        "RETRIEVAL_EVALUATION": "retrieval_evaluation",
        "MEMORY_LIFECYCLE_SWEEP": "memory_lifecycle_sweep",
    }
    for attr, value in expected.items():
        assert getattr(JobType, attr) == value
    assert set(ALL_JOB_TYPES) == set(expected.values())
    assert set(JOB_TASK_TYPES).isdisjoint(EXTERNAL_QUEUE_TYPES)


def test_dispatch_table_covers_all_job_task_types():
    assert set(job_worker.JOB_HANDLERS) == set(JOB_TASK_TYPES)
    # memory_expire / memory_revalidation 复用 memory sweep 处理器。
    assert job_worker.JOB_HANDLERS[JobType.MEMORY_EXPIRE] is job_worker.JOB_HANDLERS[JobType.MEMORY_LIFECYCLE_SWEEP]
    assert job_worker.JOB_HANDLERS[JobType.MEMORY_REVALIDATION] is job_worker.JOB_HANDLERS[JobType.MEMORY_LIFECYCLE_SWEEP]


@pytest.mark.parametrize("task_type", sorted(JOB_TASK_TYPES))
def test_dispatch_accepts_new_and_legacy_type_strings(monkeypatch, isolated_database, task_type):
    """job_worker 对新旧 task_type 字符串都能认领并派发到对应处理器。"""
    calls = []
    monkeypatch.setitem(job_worker.JOB_HANDLERS, task_type, lambda payload, ensure_lease: calls.append(payload) or {"ok": True})
    task = JobTaskRepository().create(task_type, {"probe": task_type})
    assert job_worker.process_one_job("test-worker", job_id=task["id"]) is True
    assert calls == [{"probe": task_type}]
    finished = JobTaskRepository().get(task["id"])
    assert finished["status"] == "SUCCEEDED"
    assert '"ok": true' in finished["result_ref"]


def test_external_queue_types_have_no_job_worker_handler():
    """Agent vector_index is consumed by its dedicated queue."""
    for task_type in EXTERNAL_QUEUE_TYPES:
        assert task_type not in job_worker.JOB_HANDLERS
