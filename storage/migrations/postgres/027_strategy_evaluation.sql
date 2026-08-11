CREATE TABLE IF NOT EXISTS strategy_evaluation (
  id VARCHAR(36) PRIMARY KEY,
  strategy_id VARCHAR(36) NOT NULL,
  evaluation_type VARCHAR(32) NOT NULL,
  data_as_of TIMESTAMP,
  data_snapshot_id VARCHAR(128),
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
  quality_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
  passed BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
