-- PostgreSQL immutable lineage for every DecisionInputBundle patch.
CREATE TABLE IF NOT EXISTS decision_input_bundle_patch (
    patch_id VARCHAR(64) PRIMARY KEY,
    bundle_id VARCHAR(64) NOT NULL REFERENCES decision_input_bundle(bundle_id),
    reason TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    previous_hash VARCHAR(64) NOT NULL,
    new_hash VARCHAR(64) NOT NULL,
    evidence_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE(bundle_id, previous_hash, new_hash)
);
CREATE INDEX IF NOT EXISTS idx_decision_input_bundle_patch_bundle ON decision_input_bundle_patch(bundle_id);
