-- Standalone migration tests and fresh SQLite deployments may not have ORM
-- metadata created yet, so create the legacy base table before hardening it.
CREATE TABLE IF NOT EXISTS memory_record (
    id INTEGER PRIMARY KEY,
    memory_type VARCHAR(64) NOT NULL,
    title VARCHAR(256) NOT NULL,
    content TEXT NOT NULL,
    source_type VARCHAR(64) NOT NULL,
    source_date TIMESTAMP,
    related_regime VARCHAR(64),
    related_strategy VARCHAR(64),
    related_theme VARCHAR(128),
    related_symbol VARCHAR(32),
    status VARCHAR(32) NOT NULL DEFAULT 'validated',
    importance VARCHAR(32) NOT NULL DEFAULT 'medium',
    confidence REAL NOT NULL DEFAULT 0.7,
    valid_from TIMESTAMP,
    valid_to TIMESTAMP,
    is_deleted BOOLEAN NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE memory_record ADD COLUMN subject_key VARCHAR(256);
ALTER TABLE memory_record ADD COLUMN merge_key VARCHAR(512);
ALTER TABLE memory_record ADD COLUMN temporal_class VARCHAR(32) NOT NULL DEFAULT 'SLOW_CHANGING';
ALTER TABLE memory_record ADD COLUMN facts TEXT NOT NULL DEFAULT '{}';
ALTER TABLE memory_record ADD COLUMN lessons TEXT NOT NULL DEFAULT '[]';
ALTER TABLE memory_record ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE memory_record ADD COLUMN conflict_group VARCHAR(64);
ALTER TABLE memory_record ADD COLUMN last_seen_at TIMESTAMP;
ALTER TABLE memory_record ADD COLUMN updated_at TIMESTAMP;
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_record_merge_key ON memory_record(merge_key);
CREATE INDEX IF NOT EXISTS idx_memory_record_subject_key ON memory_record(subject_key);

CREATE TABLE IF NOT EXISTS memory_version (
    id INTEGER PRIMARY KEY,
    memory_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    facts TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL,
    change_reason VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS market_regime_state (
    market_code VARCHAR(32) PRIMARY KEY,
    confirmed_regime VARCHAR(64) NOT NULL,
    confirmed_since DATE NOT NULL,
    candidate_regime VARCHAR(64),
    candidate_since DATE,
    candidate_days INTEGER NOT NULL DEFAULT 0,
    confidence REAL,
    features TEXT NOT NULL DEFAULT '{}',
    transition_reason TEXT NOT NULL DEFAULT '{}',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS market_regime_history (
    id INTEGER PRIMARY KEY,
    market_code VARCHAR(32) NOT NULL,
    previous_regime VARCHAR(64),
    new_regime VARCHAR(64) NOT NULL,
    started_at DATE NOT NULL,
    ended_at DATE,
    confidence REAL,
    evidence TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_market_regime_history_market ON market_regime_history(market_code, started_at);

CREATE TABLE IF NOT EXISTS investment_decision (
    id VARCHAR(36) PRIMARY KEY,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    query TEXT,
    skill_slug VARCHAR(128),
    market_regime VARCHAR(64),
    market_features TEXT NOT NULL DEFAULT '{}',
    thesis TEXT NOT NULL DEFAULT '{}',
    themes TEXT NOT NULL DEFAULT '[]',
    candidates TEXT NOT NULL DEFAULT '[]',
    portfolio_advice TEXT NOT NULL DEFAULT '{}',
    confidence REAL,
    trigger_conditions TEXT NOT NULL DEFAULT '[]',
    invalidation_conditions TEXT NOT NULL DEFAULT '[]',
    evidence_refs TEXT NOT NULL DEFAULT '[]',
    tool_trace TEXT NOT NULL DEFAULT '[]',
    status VARCHAR(32) NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS investment_decision_outcome (
    id INTEGER PRIMARY KEY,
    decision_id VARCHAR(36) NOT NULL,
    evaluation_date DATE NOT NULL,
    horizon_days INTEGER NOT NULL,
    benchmark_return REAL,
    portfolio_return REAL,
    excess_return REAL,
    trigger_hit BOOLEAN,
    invalidation_hit BOOLEAN,
    realized_metrics TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_decision_outcome_decision ON investment_decision_outcome(decision_id, evaluation_date);

CREATE TABLE IF NOT EXISTS decision_review (
    id INTEGER PRIMARY KEY,
    decision_id VARCHAR(36) NOT NULL,
    outcome_id INTEGER,
    decision_quality REAL,
    what_was_correct TEXT NOT NULL DEFAULT '[]',
    what_was_wrong TEXT NOT NULL DEFAULT '[]',
    root_causes TEXT NOT NULL DEFAULT '[]',
    unexpected_events TEXT NOT NULL DEFAULT '[]',
    lessons TEXT NOT NULL DEFAULT '[]',
    memory_candidate_ids TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
