CREATE TABLE IF NOT EXISTS market_trading_calendar (
    market_code VARCHAR(16) NOT NULL,
    trading_date DATE NOT NULL,
    is_open BOOLEAN NOT NULL,
    source VARCHAR(32),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (market_code, trading_date)
);
