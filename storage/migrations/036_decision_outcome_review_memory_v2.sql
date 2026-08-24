-- D7 immutable outcome/review/memory records.  Legacy tables remain intact.
CREATE TABLE IF NOT EXISTS decision_outcome_v2 (
    outcome_id VARCHAR(64) PRIMARY KEY,
    decision_id VARCHAR(64) NOT NULL REFERENCES investment_decision(id),
    outcome_hash VARCHAR(64) NOT NULL UNIQUE,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decision_outcome_v2_decision ON decision_outcome_v2(decision_id);
CREATE TABLE IF NOT EXISTS decision_review_v2 (
    review_id VARCHAR(64) PRIMARY KEY,
    decision_id VARCHAR(64) NOT NULL REFERENCES investment_decision(id),
    review_hash VARCHAR(64) NOT NULL UNIQUE,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decision_review_v2_decision ON decision_review_v2(decision_id);
CREATE TABLE IF NOT EXISTS decision_memory_v2 (
    memory_id VARCHAR(64) PRIMARY KEY,
    memory_type VARCHAR(40) NOT NULL,
    memory_hash VARCHAR(64) NOT NULL UNIQUE,
    dedupe_key VARCHAR(128) NOT NULL UNIQUE,
    status VARCHAR(24) NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decision_memory_v2_type ON decision_memory_v2(memory_type);
