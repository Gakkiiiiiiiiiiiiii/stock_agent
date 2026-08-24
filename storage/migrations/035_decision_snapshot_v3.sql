CREATE TABLE IF NOT EXISTS decision_snapshot_v3 (
    snapshot_id VARCHAR(64) PRIMARY KEY,
    decision_id VARCHAR(64) NOT NULL REFERENCES investment_decision(id),
    schema_version VARCHAR(40) NOT NULL,
    bundle_id VARCHAR(64) NOT NULL REFERENCES decision_input_bundle(bundle_id),
    snapshot_hash VARCHAR(64) NOT NULL UNIQUE,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_decision_snapshot_v3_decision_id ON decision_snapshot_v3 (decision_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_decision_snapshot_v3_decision_id ON decision_snapshot_v3 (decision_id);
CREATE INDEX IF NOT EXISTS ix_decision_snapshot_v3_bundle_id ON decision_snapshot_v3 (bundle_id);
