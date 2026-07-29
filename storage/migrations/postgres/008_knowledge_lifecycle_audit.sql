CREATE TABLE IF NOT EXISTS knowledge_lifecycle_audit (
    id BIGSERIAL PRIMARY KEY,
    knowledge_unit_id BIGINT NOT NULL,
    from_lifecycle_status VARCHAR(32),
    to_lifecycle_status VARCHAR(32),
    from_verification_status VARCHAR(32),
    to_verification_status VARCHAR(32),
    valid_to_before TIMESTAMP,
    valid_to_after TIMESTAMP,
    reason TEXT,
    operator VARCHAR(128),
    vector_task_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_knowledge_lifecycle_audit_unit ON knowledge_lifecycle_audit(knowledge_unit_id, created_at);
CREATE INDEX IF NOT EXISTS idx_knowledge_lifecycle_audit_status ON knowledge_lifecycle_audit(to_lifecycle_status, to_verification_status);
