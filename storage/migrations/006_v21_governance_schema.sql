CREATE TABLE IF NOT EXISTS data_snapshot (
    id VARCHAR(36) PRIMARY KEY,
    snapshot_type VARCHAR(64) NOT NULL,
    as_of TIMESTAMP NOT NULL,
    source VARCHAR(64) NOT NULL,
    source_version VARCHAR(128),
    data_hash VARCHAR(128) NOT NULL,
    coverage_ratio NUMERIC(8,6),
    quality_score NUMERIC(8,6),
    quality_flags TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS research_run (
    id VARCHAR(36) PRIMARY KEY,
    run_type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    request TEXT NOT NULL,
    config_version VARCHAR(64),
    code_version VARCHAR(64),
    data_snapshot_ids TEXT NOT NULL DEFAULT '[]',
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    result_summary TEXT,
    error TEXT,
    created_by VARCHAR(128),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS factor_definition (
    id VARCHAR(36) PRIMARY KEY,
    factor_code VARCHAR(64) UNIQUE NOT NULL,
    expression TEXT NOT NULL,
    rpn TEXT NOT NULL,
    hypothesis TEXT,
    created_by_model VARCHAR(128),
    lifecycle_status VARCHAR(32) NOT NULL,
    applicable_regimes TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS factor_evaluation (
    id VARCHAR(36) PRIMARY KEY,
    factor_id VARCHAR(36) NOT NULL,
    research_run_id VARCHAR(36) NOT NULL,
    split_type VARCHAR(32) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    universe_version VARCHAR(64) NOT NULL,
    metrics TEXT NOT NULL,
    passed BOOLEAN NOT NULL,
    failure_reasons TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS signal_event (
    id VARCHAR(36) PRIMARY KEY,
    symbol VARCHAR(32) NOT NULL,
    signal_type VARCHAR(64) NOT NULL,
    signal_state VARCHAR(32) NOT NULL,
    feature_time TIMESTAMP NOT NULL,
    executable_from TIMESTAMP NOT NULL,
    score NUMERIC(8,4),
    confidence NUMERIC(8,4),
    evidence TEXT NOT NULL,
    risks TEXT NOT NULL,
    algorithm_version VARCHAR(64) NOT NULL,
    data_snapshot_ids TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tool_audit_log (
    id VARCHAR(36) PRIMARY KEY,
    session_id VARCHAR(128),
    run_id VARCHAR(36),
    tool_name VARCHAR(128) NOT NULL,
    permission_level VARCHAR(32) NOT NULL,
    request_payload TEXT,
    response_summary TEXT,
    status VARCHAR(32) NOT NULL,
    latency_ms INTEGER,
    confirmation_id VARCHAR(36),
    error_code VARCHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS change_proposal (
    id VARCHAR(36) PRIMARY KEY,
    target_type VARCHAR(64) NOT NULL,
    target_id VARCHAR(128),
    diff_hash VARCHAR(128) NOT NULL,
    diff_payload TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    confirmation_token_hash VARCHAR(128),
    expires_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_task (
    id VARCHAR(36) PRIMARY KEY,
    task_type VARCHAR(64) NOT NULL,
    payload TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    progress NUMERIC(8,4) NOT NULL DEFAULT 0,
    result_ref TEXT,
    error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    idempotency_key VARCHAR(128),
    worker_id VARCHAR(128),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    heartbeat_at TIMESTAMP,
    finished_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_job_task_status ON job_task(status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_job_task_idempotency ON job_task(idempotency_key) WHERE idempotency_key IS NOT NULL;
