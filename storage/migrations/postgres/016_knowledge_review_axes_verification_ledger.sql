ALTER TABLE knowledge_unit ADD COLUMN IF NOT EXISTS review_status VARCHAR(32) NOT NULL DEFAULT 'UNREVIEWED';
ALTER TABLE knowledge_unit ADD COLUMN IF NOT EXISTS evidence_quality_status VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE knowledge_unit ADD COLUMN IF NOT EXISTS support_score DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS ix_knowledge_unit_review_status ON knowledge_unit (review_status);
CREATE INDEX IF NOT EXISTS ix_knowledge_unit_evidence_quality_status ON knowledge_unit (evidence_quality_status);
ALTER TABLE knowledge_verification ADD COLUMN IF NOT EXISTS evidence_id BIGINT;
ALTER TABLE knowledge_verification ADD COLUMN IF NOT EXISTS provenance_json TEXT NOT NULL DEFAULT '{}';
