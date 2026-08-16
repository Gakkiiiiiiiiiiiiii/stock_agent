-- 详细修改方案 §13/§25：ToolResult Snapshot（EXACT_REPLAY 使用历史工具结果，不重新联网）
CREATE TABLE IF NOT EXISTS tool_result_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_result_id VARCHAR(36) UNIQUE NOT NULL,
    decision_id VARCHAR(36),
    agent_run_id VARCHAR(64),
    tool_id VARCHAR(128) NOT NULL,
    tool_version VARCHAR(64),
    request_hash VARCHAR(64) NOT NULL,
    response_hash VARCHAR(64) NOT NULL,
    snapshot_refs JSON NOT NULL DEFAULT '[]',
    latency_ms REAL,
    status VARCHAR(16) NOT NULL DEFAULT 'OK',
    response_payload JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tool_result_decision ON tool_result_snapshots(decision_id);
CREATE INDEX IF NOT EXISTS idx_tool_result_tool ON tool_result_snapshots(tool_id, request_hash);
