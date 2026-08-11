"""统一 Worker 任务契约（设计文档 §29）：job_task task_type 常量单一事实来源。

- ``JOB_TASK_TYPES``：由 workers/job_worker.py 从 job_task 表认领并派发的类型。
- ``EXTERNAL_QUEUE_TYPES``：§29 契约中的类型，但由独立队列/入口消费
  （vector_index → workers/vector_index_worker.py 的 vector_task 表；
  content_ingestion → workers/content_ingest_worker.py 的 content_task 表；
  factor_paper_tracking → workers/factor_paper_worker.py 的 CLI/定时入口）。
  它们在此登记，保证全系统任务类型命名唯一。
- 历史类型（factor_mine / knowledge_lifecycle_sweep / memory_lifecycle_sweep）
  保持可用：已入队的存量任务继续被认领执行；memory_expire /
  memory_revalidation 复用 memory_lifecycle_sweep 的处理器。

幂等约定：
- 入队侧：JobTaskRepository.create(idempotency_key=...) 去重；
- 执行侧：处理器按 payload 关键键（如 trade_date/feature_version）upsert
  或覆盖输出，重复执行同一 payload 不产生额外副作用。
"""
from __future__ import annotations


class JobType:
    """job_task / 外部队列任务类型常量（§29）。"""

    # §29 目标类型
    VECTOR_INDEX = "vector_index"
    DECISION_OUTCOME = "decision_outcome"
    DECISION_REVIEW = "decision_review"
    MEMORY_EXPIRE = "memory_expire"
    MEMORY_REVALIDATION = "memory_revalidation"
    FACTOR_PAPER_TRACKING = "factor_paper_tracking"
    MARKET_FEATURE_SNAPSHOT = "market_feature_snapshot"
    SECTOR_FEATURE_SNAPSHOT = "sector_feature_snapshot"
    RETRIEVAL_EVALUATION = "retrieval_evaluation"
    CONTENT_INGESTION = "content_ingestion"
    # 历史类型（兼容存量已入队任务，持续可用）
    FACTOR_MINE = "factor_mine"
    KNOWLEDGE_LIFECYCLE_SWEEP = "knowledge_lifecycle_sweep"
    MEMORY_LIFECYCLE_SWEEP = "memory_lifecycle_sweep"


#: workers/job_worker.py 从 job_task 表认领的类型（含历史类型与别名）。
JOB_TASK_TYPES: tuple[str, ...] = (
    JobType.FACTOR_MINE,
    JobType.KNOWLEDGE_LIFECYCLE_SWEEP,
    JobType.MEMORY_LIFECYCLE_SWEEP,
    JobType.MEMORY_EXPIRE,
    JobType.MEMORY_REVALIDATION,
    JobType.DECISION_OUTCOME,
    JobType.DECISION_REVIEW,
    JobType.MARKET_FEATURE_SNAPSHOT,
    JobType.SECTOR_FEATURE_SNAPSHOT,
    JobType.RETRIEVAL_EVALUATION,
)

#: §29 契约中由独立队列/入口消费、不由 job_worker 派发的类型。
EXTERNAL_QUEUE_TYPES: tuple[str, ...] = (
    JobType.VECTOR_INDEX,
    JobType.CONTENT_INGESTION,
    JobType.FACTOR_PAPER_TRACKING,
)

ALL_JOB_TYPES: tuple[str, ...] = JOB_TASK_TYPES + EXTERNAL_QUEUE_TYPES
