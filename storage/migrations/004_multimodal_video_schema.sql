CREATE TABLE IF NOT EXISTS video_frame (
    id BIGSERIAL PRIMARY KEY,
    video_id BIGINT NOT NULL,
    frame_index INTEGER NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    image_path TEXT NOT NULL,
    trigger_source VARCHAR(32),
    ocr_text TEXT,
    visual_summary TEXT,
    related_text TEXT,
    themes_json TEXT DEFAULT '[]',
    symbols_json TEXT DEFAULT '[]',
    confidence_score NUMERIC(10, 4),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(video_id, frame_index)
);
