-- PostgreSQL variant of immutable, redacted conclusion lineage audits.
ALTER TABLE knowledge_conclusion_run ADD COLUMN IF NOT EXISTS audit_metadata_json TEXT;
CREATE TABLE IF NOT EXISTS knowledge_conclusion_lineage_audit (
  audit_id TEXT PRIMARY KEY,
  conclusion_id TEXT NOT NULL REFERENCES knowledge_conclusion_run(conclusion_id),
  audit_hash TEXT NOT NULL UNIQUE,
  payload_json TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL
);
CREATE OR REPLACE RULE knowledge_conclusion_lineage_audit_no_update AS ON UPDATE TO knowledge_conclusion_lineage_audit DO INSTEAD NOTHING;
CREATE OR REPLACE RULE knowledge_conclusion_lineage_audit_no_delete AS ON DELETE TO knowledge_conclusion_lineage_audit DO INSTEAD NOTHING;
