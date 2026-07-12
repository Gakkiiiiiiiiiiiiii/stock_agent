CREATE TABLE IF NOT EXISTS vector_index_mapping (
    id BIGSERIAL PRIMARY KEY,
    postgres_table VARCHAR(128) NOT NULL,
    postgres_id BIGINT NOT NULL,
    chunk_id VARCHAR(256) NOT NULL,
    qdrant_collection VARCHAR(128) NOT NULL,
    qdrant_point_id VARCHAR(128) NOT NULL,
    embedding_model VARCHAR(128),
    embedding_version VARCHAR(64),
    sparse_model VARCHAR(128),
    reranker_model VARCHAR(128),
    content_hash VARCHAR(128),
    index_status VARCHAR(32),
    last_indexed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(postgres_table, postgres_id, chunk_id)
);

CREATE TABLE IF NOT EXISTS vector_index_task (
    id BIGSERIAL PRIMARY KEY,
    task_type VARCHAR(32),
    postgres_table VARCHAR(128),
    postgres_id BIGINT,
    target_collection VARCHAR(128),
    status VARCHAR(32),
    retry_count INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS memory_record (
    id BIGSERIAL PRIMARY KEY,
    memory_type VARCHAR(64),
    title VARCHAR(256),
    content TEXT,
    source_type VARCHAR(64),
    source_date TIMESTAMP,
    related_regime VARCHAR(64),
    related_strategy VARCHAR(64),
    related_theme VARCHAR(128),
    related_symbol VARCHAR(32),
    status VARCHAR(32) DEFAULT 'validated',
    importance VARCHAR(32) DEFAULT 'medium',
    confidence NUMERIC(10, 4) DEFAULT 0.70,
    valid_from TIMESTAMP,
    valid_to TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market_regime_label (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    universe VARCHAR(64) NOT NULL,
    decision_mode VARCHAR(32),
    label_type VARCHAR(32),
    primary_regime VARCHAR(64),
    secondary_regime VARCHAR(64),
    confidence NUMERIC(10, 4),
    label_source VARCHAR(32),
    created_at TIMESTAMP DEFAULT NOW()
);
