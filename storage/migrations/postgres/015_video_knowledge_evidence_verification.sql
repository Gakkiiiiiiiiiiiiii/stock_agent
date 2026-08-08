ALTER TABLE knowledge_unit ADD COLUMN IF NOT EXISTS support_status VARCHAR(32) NOT NULL DEFAULT 'UNSUPPORTED';
ALTER TABLE knowledge_unit ADD COLUMN IF NOT EXISTS support_probability DOUBLE PRECISION;
ALTER TABLE knowledge_unit ADD COLUMN IF NOT EXISTS truth_status VARCHAR(32) NOT NULL DEFAULT 'NOT_EXTERNALLY_VERIFIED';
ALTER TABLE knowledge_unit ADD COLUMN IF NOT EXISTS external_verification_status VARCHAR(32) NOT NULL DEFAULT 'NOT_RUN';
ALTER TABLE knowledge_unit ADD COLUMN IF NOT EXISTS source_reliability_score DOUBLE PRECISION;
ALTER TABLE knowledge_unit ADD COLUMN IF NOT EXISTS speaker_id VARCHAR(128);
ALTER TABLE knowledge_unit ADD COLUMN IF NOT EXISTS speaker_name VARCHAR(256);
ALTER TABLE knowledge_unit ADD COLUMN IF NOT EXISTS attribution_confidence DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS ix_knowledge_unit_support_status ON knowledge_unit (support_status);
CREATE INDEX IF NOT EXISTS ix_knowledge_unit_truth_status ON knowledge_unit (truth_status);

ALTER TABLE knowledge_evidence ADD COLUMN IF NOT EXISTS raw_text TEXT;
ALTER TABLE knowledge_evidence ADD COLUMN IF NOT EXISTS normalized_text TEXT;
ALTER TABLE knowledge_evidence ADD COLUMN IF NOT EXISTS word_timestamps_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE knowledge_evidence ADD COLUMN IF NOT EXISTS bbox_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE knowledge_evidence ADD COLUMN IF NOT EXISTS asr_metrics_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE knowledge_evidence ADD COLUMN IF NOT EXISTS ocr_metrics_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE knowledge_evidence ADD COLUMN IF NOT EXISTS correction_trace_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE knowledge_evidence ADD COLUMN IF NOT EXISTS evidence_hash VARCHAR(64);
ALTER TABLE knowledge_evidence ADD COLUMN IF NOT EXISTS semantic_support_score DOUBLE PRECISION;
ALTER TABLE knowledge_evidence ADD COLUMN IF NOT EXISTS numeric_consistency_score DOUBLE PRECISION;
ALTER TABLE knowledge_evidence ADD COLUMN IF NOT EXISTS entity_consistency_score DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS ix_knowledge_evidence_hash ON knowledge_evidence (evidence_hash);
CREATE TABLE IF NOT EXISTS knowledge_verification (
    id BIGSERIAL PRIMARY KEY,
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
