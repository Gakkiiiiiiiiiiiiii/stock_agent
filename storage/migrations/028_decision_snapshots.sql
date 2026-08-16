-- 028: DecisionSnapshot（设计文档 §26 / §82 / §109）
CREATE TABLE IF NOT EXISTS decision_snapshots (
    snapshot_id VARCHAR(36) PRIMARY KEY,
    decision_id VARCHAR(36) NOT NULL,
    decision_time TIMESTAMP,
    market JSON,
    content JSON,
    factor JSON,
    strategy JSON,
    agent JSON,
    portfolio JSON,
    risk JSON,
    lineage JSON,
    decision_quality VARCHAR(16),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_decision_snapshots_decision_id ON decision_snapshots (decision_id);
