CREATE TABLE IF NOT EXISTS video_chunk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES video_asset(id),
    chunk_index INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    topic VARCHAR(512),
    transcript_text TEXT NOT NULL,
    ocr_text TEXT,
    visual_focus TEXT,
    entities_json TEXT NOT NULL DEFAULT '[]',
    visual_tags_json TEXT NOT NULL DEFAULT '[]',
    confidence_score FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(video_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS financial_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES video_asset(id),
    chunk_id INTEGER REFERENCES video_chunk(id),
    event_index INTEGER NOT NULL DEFAULT 0,
    event_type VARCHAR(64) NOT NULL,
    claim_type VARCHAR(32),
    sentiment VARCHAR(32),
    subjectivity VARCHAR(32),
    certainty FLOAT,
    confidence_score FLOAT,
    statement TEXT NOT NULL,
    time_expression VARCHAR(255),
    normalized_time_start VARCHAR(64),
    normalized_time_end VARCHAR(64),
    start_ms INTEGER,
    end_ms INTEGER,
    condition_text TEXT,
    invalidation_text TEXT,
    entities_json TEXT NOT NULL DEFAULT '[]',
    attributes_json TEXT NOT NULL DEFAULT '{}',
    conflict_key VARCHAR(256),
    conflict_status VARCHAR(32),
    superseded_by_event_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES financial_event(id),
    source_type VARCHAR(32) NOT NULL,
    source_id VARCHAR(128),
    evidence_text TEXT NOT NULL,
    timestamp_ms INTEGER,
    confidence_score FLOAT,
    image_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
