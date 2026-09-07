-- Immutable, redacted lineage audit for content-only knowledge conclusions.
ALTER TABLE knowledge_conclusion_run ADD COLUMN audit_metadata_json TEXT;
CREATE TABLE IF NOT EXISTS knowledge_conclusion_lineage_audit (
  audit_id TEXT PRIMARY KEY,
  conclusion_id TEXT NOT NULL REFERENCES knowledge_conclusion_run(conclusion_id),
  audit_hash TEXT NOT NULL UNIQUE,
  payload_json TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL
);
CREATE TRIGGER IF NOT EXISTS knowledge_conclusion_lineage_audit_no_update
BEFORE UPDATE ON knowledge_conclusion_lineage_audit
BEGIN SELECT RAISE(ABORT, 'KNOWLEDGE_CONCLUSION_LINEAGE_AUDIT_IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS knowledge_conclusion_lineage_audit_no_delete
BEFORE DELETE ON knowledge_conclusion_lineage_audit
BEGIN SELECT RAISE(ABORT, 'KNOWLEDGE_CONCLUSION_LINEAGE_AUDIT_IMMUTABLE'); END;
