ALTER TABLE memory_record ADD COLUMN IF NOT EXISTS applicable_market VARCHAR(64);
ALTER TABLE memory_record ADD COLUMN IF NOT EXISTS applicable_regimes JSONB;
ALTER TABLE memory_record ADD COLUMN IF NOT EXISTS applicable_styles JSONB;
ALTER TABLE memory_record ADD COLUMN IF NOT EXISTS applicable_horizon INTEGER;
ALTER TABLE memory_record ADD COLUMN IF NOT EXISTS applicable_themes JSONB;
CREATE TABLE IF NOT EXISTS memory_evidence (
    id SERIAL PRIMARY KEY,
    memory_id INTEGER NOT NULL,
    decision_id VARCHAR(64),
    regime VARCHAR(64),
    horizon_days INTEGER,
    market_excess_return DOUBLE PRECISION,
    sector_excess_return DOUBLE PRECISION,
    decision_quality DOUBLE PRECISION,
    applicability DOUBLE PRECISION,
    weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_memory_evidence_memory_id ON memory_evidence (memory_id);
CREATE INDEX IF NOT EXISTS ix_memory_evidence_decision_id ON memory_evidence (decision_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_memory_evidence_event ON memory_evidence (memory_id, decision_id, horizon_days);
