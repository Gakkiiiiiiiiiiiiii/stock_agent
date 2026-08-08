ALTER TABLE knowledge_unit ADD COLUMN support_status VARCHAR(32) NOT NULL DEFAULT 'UNSUPPORTED';
ALTER TABLE knowledge_unit ADD COLUMN support_probability DOUBLE PRECISION;
ALTER TABLE knowledge_unit ADD COLUMN truth_status VARCHAR(32) NOT NULL DEFAULT 'NOT_EXTERNALLY_VERIFIED';
ALTER TABLE knowledge_unit ADD COLUMN external_verification_status VARCHAR(32) NOT NULL DEFAULT 'NOT_RUN';
ALTER TABLE knowledge_unit ADD COLUMN source_reliability_score DOUBLE PRECISION;
ALTER TABLE knowledge_unit ADD COLUMN speaker_id VARCHAR(128);
ALTER TABLE knowledge_unit ADD COLUMN speaker_name VARCHAR(256);
ALTER TABLE knowledge_unit ADD COLUMN attribution_confidence DOUBLE PRECISION;
ALTER TABLE knowledge_evidence ADD COLUMN raw_text TEXT;
ALTER TABLE knowledge_evidence ADD COLUMN normalized_text TEXT;
ALTER TABLE knowledge_evidence ADD COLUMN word_timestamps_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE knowledge_evidence ADD COLUMN bbox_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE knowledge_evidence ADD COLUMN asr_metrics_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE knowledge_evidence ADD COLUMN ocr_metrics_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE knowledge_evidence ADD COLUMN correction_trace_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE knowledge_evidence ADD COLUMN evidence_hash VARCHAR(64);
ALTER TABLE knowledge_evidence ADD COLUMN semantic_support_score DOUBLE PRECISION;
ALTER TABLE knowledge_evidence ADD COLUMN numeric_consistency_score DOUBLE PRECISION;
ALTER TABLE knowledge_evidence ADD COLUMN entity_consistency_score DOUBLE PRECISION;
CREATE TABLE IF NOT EXISTS knowledge_verification (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_unit_id BIGINT NOT NULL,
    verifier_type VARCHAR(64) NOT NULL,
    verifier_provider VARCHAR(128),
    verifier_model VARCHAR(128),
    verifier_version VARCHAR(64),
    status VARCHAR(32) NOT NULL,
    score DOUBLE PRECISION,
    checks_json TEXT NOT NULL DEFAULT '{}',
    reason_codes_json TEXT NOT NULL DEFAULT '[]',
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_knowledge_verification_unit ON knowledge_verification (knowledge_unit_id);
