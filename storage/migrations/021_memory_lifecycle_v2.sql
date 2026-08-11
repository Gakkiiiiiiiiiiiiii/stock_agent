ALTER TABLE memory_record ADD COLUMN applicable_market VARCHAR(64);
ALTER TABLE memory_record ADD COLUMN applicable_regimes JSON;
ALTER TABLE memory_record ADD COLUMN applicable_styles JSON;
ALTER TABLE memory_record ADD COLUMN applicable_horizon INTEGER;
ALTER TABLE memory_record ADD COLUMN applicable_themes JSON;
CREATE TABLE IF NOT EXISTS memory_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL,
    decision_id VARCHAR(64),
    regime VARCHAR(64),
    horizon_days INTEGER,
    market_excess_return FLOAT,
    sector_excess_return FLOAT,
    decision_quality FLOAT,
    applicability FLOAT,
    weight FLOAT NOT NULL DEFAULT 1.0,
    created_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_memory_evidence_memory_id ON memory_evidence (memory_id);
CREATE INDEX IF NOT EXISTS ix_memory_evidence_decision_id ON memory_evidence (decision_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_memory_evidence_event ON memory_evidence (memory_id, decision_id, horizon_days);
