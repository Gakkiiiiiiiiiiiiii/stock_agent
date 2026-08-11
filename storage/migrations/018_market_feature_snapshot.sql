CREATE TABLE IF NOT EXISTS market_feature_snapshot (
    id INTEGER PRIMARY KEY,
    market_code VARCHAR(32) NOT NULL,
    as_of TIMESTAMP NOT NULL,
    trade_date DATE NOT NULL,
    feature_version VARCHAR(64) NOT NULL,
    features_json TEXT NOT NULL DEFAULT '{}',
    quality_score REAL,
    quality_flags TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_market_feature_snapshot_key ON market_feature_snapshot(market_code, trade_date, feature_version);
CREATE INDEX IF NOT EXISTS ix_market_feature_snapshot_market_code ON market_feature_snapshot(market_code);
CREATE INDEX IF NOT EXISTS ix_market_feature_snapshot_trade_date ON market_feature_snapshot(trade_date);

CREATE TABLE IF NOT EXISTS sector_feature_snapshot (
    id INTEGER PRIMARY KEY,
    sector_code VARCHAR(32),
    sector_name VARCHAR(128) NOT NULL,
    trade_date DATE NOT NULL,
    as_of TIMESTAMP NOT NULL,
    component_scores TEXT NOT NULL DEFAULT '{}',
    final_score REAL NOT NULL,
    universe_size INTEGER NOT NULL DEFAULT 0,
    valid_symbol_count INTEGER NOT NULL DEFAULT 0,
    coverage REAL NOT NULL DEFAULT 0,
    feature_version VARCHAR(64) NOT NULL,
    quality_flags TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sector_feature_snapshot_key ON sector_feature_snapshot(sector_name, trade_date, feature_version);
CREATE INDEX IF NOT EXISTS ix_sector_feature_snapshot_sector_code ON sector_feature_snapshot(sector_code);
CREATE INDEX IF NOT EXISTS ix_sector_feature_snapshot_sector_name ON sector_feature_snapshot(sector_name);
CREATE INDEX IF NOT EXISTS ix_sector_feature_snapshot_trade_date ON sector_feature_snapshot(trade_date);

CREATE TABLE IF NOT EXISTS symbol_sector_membership (
    id INTEGER PRIMARY KEY,
    symbol VARCHAR(32) NOT NULL,
    sector_code VARCHAR(32) NOT NULL,
    sector_name VARCHAR(128) NOT NULL,
    valid_from DATE NOT NULL,
    valid_to DATE,
    source VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_symbol_sector_membership_symbol ON symbol_sector_membership(symbol);
CREATE INDEX IF NOT EXISTS ix_symbol_sector_membership_sector_code ON symbol_sector_membership(sector_code);
CREATE INDEX IF NOT EXISTS ix_symbol_sector_membership_symbol_valid_from ON symbol_sector_membership(symbol, valid_from);
