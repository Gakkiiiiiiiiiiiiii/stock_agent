-- Isolated content-only conclusion persistence.  Never references formal decisions.
CREATE TABLE IF NOT EXISTS knowledge_conclusion_run (
  conclusion_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  request_hash TEXT NOT NULL,
  request_json TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  state TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 0,
  frozen_bundle_id TEXT,
  frozen_bundle_hash TEXT,
  frozen_bundle_json TEXT,
  producer_sha TEXT,
  contract_checksum TEXT,
  model_request_id TEXT,
  model_provider_idempotency_key TEXT,
  sealed_model_response_json TEXT,
  result_json TEXT,
  result_hash TEXT,
  error_code TEXT,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_conclusion_citation (
  conclusion_id TEXT NOT NULL REFERENCES knowledge_conclusion_run(conclusion_id),
  finding_index INTEGER NOT NULL,
  knowledge_id TEXT NOT NULL,
  evidence_id TEXT NOT NULL,
  quote_hash TEXT NOT NULL,
  PRIMARY KEY (conclusion_id, finding_index, knowledge_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS knowledge_conclusion_replay_audit (
  audit_id TEXT PRIMARY KEY,
  conclusion_id TEXT NOT NULL REFERENCES knowledge_conclusion_run(conclusion_id),
  mode TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
