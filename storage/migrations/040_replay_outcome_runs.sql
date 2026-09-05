CREATE TABLE IF NOT EXISTS decision_replay_runs (
  replay_run_id TEXT PRIMARY KEY,
  decision_snapshot_id TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  expected_output_hash TEXT,
  actual_output_hash TEXT,
  status TEXT NOT NULL,
  discrepancy_artifact_id TEXT,
  owner_id TEXT,
  fencing_token INTEGER NOT NULL DEFAULT 0,
  lease_expires_at TIMESTAMP,
  UNIQUE (decision_snapshot_id, input_hash)
);
CREATE TABLE IF NOT EXISTS decision_outcome_runs (
  outcome_run_id TEXT PRIMARY KEY,
  decision_snapshot_id TEXT NOT NULL,
  result_hash TEXT,
  result_json TEXT,
  status TEXT NOT NULL,
  owner_id TEXT,
  fencing_token INTEGER NOT NULL DEFAULT 0,
  lease_expires_at TIMESTAMP,
  UNIQUE (decision_snapshot_id)
);
