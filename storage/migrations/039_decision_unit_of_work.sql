-- Expand-only migration for idempotent, atomic formal decision work units.
CREATE TABLE IF NOT EXISTS decision_requests (
  request_id TEXT PRIMARY KEY,
  portfolio_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (portfolio_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS decision_runs (
  decision_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL REFERENCES decision_requests(request_id),
  state TEXT NOT NULL,
  decision_bundle_id TEXT,
  bundle_hash TEXT,
  formal_result_hash TEXT,
  governance_hash TEXT,
  final_response_json TEXT,
  final_response_hash TEXT,
  lineage_json TEXT,
  execution_authorization_json TEXT,
  snapshot_id TEXT,
  readiness_snapshot_json TEXT,
  version INTEGER NOT NULL DEFAULT 0,
  last_error_code TEXT,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS decision_outbox (
  event_id TEXT PRIMARY KEY,
  aggregate_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload TEXT NOT NULL,
  published_at TIMESTAMP
);
-- Canonical outbox name used by publishers. The compatibility table above is
-- retained during expand deployments.
CREATE TABLE IF NOT EXISTS outbox (
  event_id TEXT PRIMARY KEY,
  aggregate_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload TEXT NOT NULL,
  published_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS decision_lineage (
  decision_id TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
