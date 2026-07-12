CREATE TABLE IF NOT EXISTS video_asset (
    id BIGSERIAL PRIMARY KEY,
    platform VARCHAR(32) NOT NULL,
    platform_video_id VARCHAR(128) NOT NULL,
    bvid VARCHAR(64),
    url TEXT NOT NULL,
    title VARCHAR(512) NOT NULL,
    author_name VARCHAR(256),
    author_id VARCHAR(128),
    publish_time_raw VARCHAR(32),
    duration_seconds INTEGER,
    cover_url TEXT,
    description TEXT,
    audio_path TEXT,
    transcript_text TEXT,
    transcript_language VARCHAR(32),
    transcript_status VARCHAR(32) DEFAULT 'pending',
    asr_provider VARCHAR(64),
    asr_model VARCHAR(128),
    source_hash VARCHAR(128),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(platform, platform_video_id)
);

CREATE TABLE IF NOT EXISTS video_segment (
    id BIGSERIAL PRIMARY KEY,
    video_id BIGINT NOT NULL,
    segment_index INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    speaker_label VARCHAR(64),
    text TEXT NOT NULL,
    avg_logprob NUMERIC(10, 4),
    no_speech_prob NUMERIC(10, 4),
    compression_ratio NUMERIC(10, 4),
    confidence_score NUMERIC(10, 4),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(video_id, segment_index)
);

CREATE TABLE IF NOT EXISTS video_summary (
    id BIGSERIAL PRIMARY KEY,
    video_id BIGINT NOT NULL,
    summary_mode VARCHAR(64) NOT NULL,
    summary_markdown TEXT NOT NULL,
    core_summary TEXT NOT NULL,
    bull_points_json TEXT DEFAULT '[]',
    bear_points_json TEXT DEFAULT '[]',
    themes_json TEXT DEFAULT '[]',
    symbols_json TEXT DEFAULT '[]',
    catalysts_json TEXT DEFAULT '[]',
    risks_json TEXT DEFAULT '[]',
    actionable_view TEXT,
    evidence_segments_json TEXT DEFAULT '[]',
    confidence_score NUMERIC(10, 4),
    llm_provider VARCHAR(64),
    llm_model VARCHAR(128),
    memory_record_id BIGINT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(video_id, summary_mode)
);

CREATE TABLE IF NOT EXISTS content_ingest_task (
    id BIGSERIAL PRIMARY KEY,
    source_type VARCHAR(64) NOT NULL,
    source_ref TEXT NOT NULL,
    video_id BIGINT,
    status VARCHAR(32) DEFAULT 'pending',
    stage VARCHAR(64) DEFAULT 'queued',
    progress INTEGER DEFAULT 0,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    options_json TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
