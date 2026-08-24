-- PostgreSQL variant of immutable DecisionInputBundle/Evidence references.
CREATE TABLE IF NOT EXISTS decision_input_bundle (
    bundle_id VARCHAR(64) PRIMARY KEY,
    schema_version VARCHAR(40) NOT NULL,
    decision_time TIMESTAMP WITH TIME ZONE NOT NULL,
    task_type VARCHAR(128) NOT NULL,
    objective TEXT NOT NULL,
    subjects_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    bundle_hash VARCHAR(64) NOT NULL UNIQUE,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_decision_input_bundle_time ON decision_input_bundle(decision_time);
CREATE TABLE IF NOT EXISTS evidence_ref (
    id BIGSERIAL PRIMARY KEY,
    bundle_id VARCHAR(64) NOT NULL REFERENCES decision_input_bundle(bundle_id),
    evidence_id VARCHAR(160) NOT NULL,
    evidence_type VARCHAR(64) NOT NULL,
    source_system VARCHAR(32) NOT NULL,
    source_ref VARCHAR(256),
    snapshot_id VARCHAR(128),
    contract_version VARCHAR(64) NOT NULL,
    as_of TIMESTAMP WITH TIME ZONE NOT NULL,
    available_at TIMESTAMP WITH TIME ZONE NOT NULL,
    quality_status VARCHAR(32) NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(bundle_id, evidence_id)
);
CREATE INDEX IF NOT EXISTS idx_evidence_ref_bundle ON evidence_ref(bundle_id);
