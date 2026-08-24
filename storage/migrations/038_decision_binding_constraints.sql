-- Additive integrity constraints for immutable v3 and D7 records.
CREATE TABLE IF NOT EXISTS decision_bundle_binding (
    decision_id VARCHAR(64) PRIMARY KEY REFERENCES investment_decision(id),
    bundle_id VARCHAR(64) NOT NULL UNIQUE REFERENCES decision_input_bundle(bundle_id),
    created_at TIMESTAMP NOT NULL
);
INSERT INTO decision_bundle_binding (decision_id, bundle_id, created_at)
SELECT decision_id, bundle_id, MIN(created_at) FROM (
    SELECT decision_id, bundle_id, created_at FROM decision_snapshot_v3
    UNION
    SELECT decision_id, bundle_id, created_at FROM specialist_artifact_v2
    UNION
    SELECT decision_id, bundle_id, created_at FROM investment_proposal_v2
    UNION
    SELECT decision_id, bundle_id, created_at FROM policy_evaluation_v2
) GROUP BY decision_id, bundle_id;
CREATE UNIQUE INDEX IF NOT EXISTS ux_decision_snapshot_v3_bundle_id
    ON decision_snapshot_v3 (bundle_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_decision_memory_v2_dedupe_key
    ON decision_memory_v2 (dedupe_key);
