CREATE TABLE IF NOT EXISTS specialist_artifact_v2 (
    artifact_id VARCHAR(64) PRIMARY KEY,
    decision_id VARCHAR(64) NOT NULL REFERENCES investment_decision(id),
    bundle_id VARCHAR(64) NOT NULL REFERENCES decision_input_bundle(bundle_id),
    artifact_hash VARCHAR(64) NOT NULL UNIQUE,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS investment_proposal_v2 (
    proposal_id VARCHAR(64) PRIMARY KEY,
    decision_id VARCHAR(64) NOT NULL REFERENCES investment_decision(id),
    bundle_id VARCHAR(64) NOT NULL REFERENCES decision_input_bundle(bundle_id),
    proposal_hash VARCHAR(64) NOT NULL UNIQUE,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS policy_evaluation_v2 (
    policy_result_id VARCHAR(64) PRIMARY KEY,
    decision_id VARCHAR(64) NOT NULL REFERENCES investment_decision(id),
    bundle_id VARCHAR(64) NOT NULL REFERENCES decision_input_bundle(bundle_id),
    result_hash VARCHAR(64) NOT NULL UNIQUE,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL
);
