CREATE TABLE IF NOT EXISTS video_chapter (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL,
    chapter_index INTEGER NOT NULL,
    parent_chapter_id INTEGER,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    title VARCHAR(512) NOT NULL,
    chapter_type VARCHAR(64) NOT NULL,
    primary_domain VARCHAR(32) NOT NULL,
    secondary_domains_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT,
    entities_json TEXT NOT NULL DEFAULT '[]',
    boundary_source VARCHAR(32),
    boundary_score FLOAT,
    confidence_score FLOAT,
    content_hash VARCHAR(128) NOT NULL,
    parser_version VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(video_id, chapter_index)
);

CREATE TABLE IF NOT EXISTS knowledge_unit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_uid VARCHAR(64) UNIQUE NOT NULL,
    source_video_id INTEGER NOT NULL,
    source_chapter_id INTEGER NOT NULL,
    primary_domain VARCHAR(32) NOT NULL,
    secondary_domains_json TEXT NOT NULL DEFAULT '[]',
    knowledge_kind VARCHAR(32) NOT NULL,
    temporal_class VARCHAR(32) NOT NULL,
    expression_type VARCHAR(32) NOT NULL,
    subject_type VARCHAR(32),
    subject_key VARCHAR(128),
    subject_name VARCHAR(256),
    predicate_key VARCHAR(128),
    statement TEXT NOT NULL,
    canonical_statement TEXT NOT NULL,
    claim_type VARCHAR(32),
    sentiment VARCHAR(32),
    certainty_score FLOAT,
    extraction_confidence FLOAT,
    as_of_time TIMESTAMP,
    valid_from TIMESTAMP,
    valid_to TIMESTAMP,
    time_horizon VARCHAR(32),
    timeframe VARCHAR(32),
    decay_half_life_days FLOAT,
    condition_text TEXT,
    invalidation_text TEXT,
    lifecycle_status VARCHAR(32) NOT NULL DEFAULT 'EXTRACTED',
    verification_status VARCHAR(32) NOT NULL DEFAULT 'UNVERIFIED',
    scope_type VARCHAR(32),
    scope_key VARCHAR(128),
    conflict_key VARCHAR(256),
    conflict_group_id VARCHAR(64),
    superseded_by_unit_id INTEGER,
    content_hash VARCHAR(128) NOT NULL,
    semantic_hash VARCHAR(128),
    attributes_json TEXT NOT NULL DEFAULT '{}',
    extractor_provider VARCHAR(64),
    extractor_model VARCHAR(128),
    extractor_version VARCHAR(64) NOT NULL,
    schema_version VARCHAR(32) NOT NULL DEFAULT 'v1',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_video_id, source_chapter_id, content_hash)
);

CREATE TABLE IF NOT EXISTS knowledge_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_unit_id INTEGER NOT NULL,
    source_type VARCHAR(32) NOT NULL,
    source_ref VARCHAR(128),
    evidence_text TEXT NOT NULL,
    start_ms INTEGER,
    end_ms INTEGER,
    frame_id INTEGER,
    confidence_score FLOAT,
    is_primary BOOLEAN NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS knowledge_entity_relation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_unit_id INTEGER NOT NULL,
    entity_type VARCHAR(32) NOT NULL,
    entity_key VARCHAR(128),
    entity_name VARCHAR(256) NOT NULL,
    ticker VARCHAR(32),
    relation_role VARCHAR(32) NOT NULL,
    confidence_score FLOAT
);

CREATE TABLE IF NOT EXISTS knowledge_unit_relation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_unit_id INTEGER NOT NULL,
    target_unit_id INTEGER NOT NULL,
    relation_type VARCHAR(32) NOT NULL,
    confidence_score FLOAT,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_unit_id, target_unit_id, relation_type)
);

CREATE TABLE IF NOT EXISTS video_analysis_document (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER UNIQUE NOT NULL,
    document_markdown TEXT NOT NULL,
    core_summary TEXT NOT NULL,
    video_type VARCHAR(64) NOT NULL,
    primary_domains_json TEXT NOT NULL DEFAULT '[]',
    chapter_count INTEGER NOT NULL,
    knowledge_unit_count INTEGER NOT NULL,
    method_count INTEGER NOT NULL DEFAULT 0,
    fact_count INTEGER NOT NULL DEFAULT 0,
    state_count INTEGER NOT NULL DEFAULT 0,
    thesis_count INTEGER NOT NULL DEFAULT 0,
    forecast_count INTEGER NOT NULL DEFAULT 0,
    action_count INTEGER NOT NULL DEFAULT 0,
    risk_count INTEGER NOT NULL DEFAULT 0,
    confidence_score FLOAT,
    generator_provider VARCHAR(64),
    generator_model VARCHAR(128),
    generator_version VARCHAR(64) NOT NULL,
    schema_version VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS knowledge_extraction_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uid VARCHAR(64) UNIQUE NOT NULL,
    video_id INTEGER NOT NULL,
    source_hash VARCHAR(128) NOT NULL,
    parser_version VARCHAR(64) NOT NULL,
    extractor_version VARCHAR(64) NOT NULL,
    schema_version VARCHAR(32) NOT NULL,
    provider VARCHAR(64),
    model VARCHAR(128),
    status VARCHAR(32) NOT NULL,
    stage VARCHAR(64),
    chapter_count INTEGER NOT NULL DEFAULT 0,
    knowledge_unit_count INTEGER NOT NULL DEFAULT 0,
    degraded BOOLEAN NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_video_chapter_video_index ON video_chapter(video_id, chapter_index);
CREATE INDEX IF NOT EXISTS idx_video_chapter_domain ON video_chapter(primary_domain, chapter_type);
CREATE INDEX IF NOT EXISTS idx_knowledge_unit_domain_kind ON knowledge_unit(primary_domain, knowledge_kind);
CREATE INDEX IF NOT EXISTS idx_knowledge_unit_subject_time ON knowledge_unit(subject_key, as_of_time);
CREATE INDEX IF NOT EXISTS idx_knowledge_unit_current ON knowledge_unit(lifecycle_status, valid_to);
CREATE INDEX IF NOT EXISTS idx_knowledge_unit_conflict ON knowledge_unit(conflict_key, lifecycle_status, as_of_time);
CREATE INDEX IF NOT EXISTS idx_knowledge_unit_video_chapter ON knowledge_unit(source_video_id, source_chapter_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_unit_scope ON knowledge_unit(scope_type, scope_key);
CREATE INDEX IF NOT EXISTS idx_knowledge_entity_key ON knowledge_entity_relation(entity_key, knowledge_unit_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_entity_ticker ON knowledge_entity_relation(ticker, knowledge_unit_id);
