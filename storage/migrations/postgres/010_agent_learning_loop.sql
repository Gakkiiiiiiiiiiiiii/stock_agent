ALTER TABLE memory_record ADD COLUMN IF NOT EXISTS subject_key VARCHAR(256);
ALTER TABLE memory_record ADD COLUMN IF NOT EXISTS merge_key VARCHAR(512);
ALTER TABLE memory_record ADD COLUMN IF NOT EXISTS temporal_class VARCHAR(32) NOT NULL DEFAULT 'SLOW_CHANGING';
ALTER TABLE memory_record ADD COLUMN IF NOT EXISTS facts JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE memory_record ADD COLUMN IF NOT EXISTS lessons JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE memory_record ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE memory_record ADD COLUMN IF NOT EXISTS conflict_group VARCHAR(64);
ALTER TABLE memory_record ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
ALTER TABLE memory_record ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_record_merge_key ON memory_record(merge_key);
CREATE INDEX IF NOT EXISTS idx_memory_record_subject_key ON memory_record(subject_key);

CREATE TABLE IF NOT EXISTS memory_version (
    id BIGSERIAL PRIMARY KEY, memory_id BIGINT NOT NULL, version INTEGER NOT NULL, content TEXT NOT NULL,
    facts JSONB NOT NULL DEFAULT '{}'::jsonb, confidence DOUBLE PRECISION NOT NULL, change_reason VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS market_regime_state (
    market_code VARCHAR(32) PRIMARY KEY, confirmed_regime VARCHAR(64) NOT NULL, confirmed_since DATE NOT NULL,
    candidate_regime VARCHAR(64), candidate_since DATE, candidate_days INTEGER NOT NULL DEFAULT 0,
    confidence DOUBLE PRECISION, features JSONB NOT NULL DEFAULT '{}'::jsonb,
    transition_reason JSONB NOT NULL DEFAULT '{}'::jsonb, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS market_regime_history (
    id BIGSERIAL PRIMARY KEY, market_code VARCHAR(32) NOT NULL, previous_regime VARCHAR(64), new_regime VARCHAR(64) NOT NULL,
    started_at DATE NOT NULL, ended_at DATE, confidence DOUBLE PRECISION, evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_market_regime_history_market ON market_regime_history(market_code, started_at);
CREATE TABLE IF NOT EXISTS investment_decision (
    id VARCHAR(36) PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), query TEXT, skill_slug VARCHAR(128), market_regime VARCHAR(64),
    market_features JSONB NOT NULL DEFAULT '{}'::jsonb, thesis JSONB NOT NULL DEFAULT '{}'::jsonb, themes JSONB NOT NULL DEFAULT '[]'::jsonb,
    candidates JSONB NOT NULL DEFAULT '[]'::jsonb, portfolio_advice JSONB NOT NULL DEFAULT '{}'::jsonb, confidence DOUBLE PRECISION,
    trigger_conditions JSONB NOT NULL DEFAULT '[]'::jsonb, invalidation_conditions JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb, tool_trace JSONB NOT NULL DEFAULT '[]'::jsonb, status VARCHAR(32) NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS investment_decision_outcome (
    id BIGSERIAL PRIMARY KEY, decision_id VARCHAR(36) NOT NULL, evaluation_date DATE NOT NULL, horizon_days INTEGER NOT NULL,
    benchmark_return DOUBLE PRECISION, portfolio_return DOUBLE PRECISION, excess_return DOUBLE PRECISION,
    trigger_hit BOOLEAN, invalidation_hit BOOLEAN, realized_metrics JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_decision_outcome_decision ON investment_decision_outcome(decision_id, evaluation_date);
CREATE TABLE IF NOT EXISTS decision_review (
    id BIGSERIAL PRIMARY KEY, decision_id VARCHAR(36) NOT NULL, outcome_id BIGINT, decision_quality DOUBLE PRECISION,
    what_was_correct JSONB NOT NULL DEFAULT '[]'::jsonb, what_was_wrong JSONB NOT NULL DEFAULT '[]'::jsonb,
    root_causes JSONB NOT NULL DEFAULT '[]'::jsonb, unexpected_events JSONB NOT NULL DEFAULT '[]'::jsonb,
    lessons JSONB NOT NULL DEFAULT '[]'::jsonb, memory_candidate_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
