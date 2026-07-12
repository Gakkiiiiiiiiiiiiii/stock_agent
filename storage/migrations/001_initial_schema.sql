CREATE TABLE IF NOT EXISTS theme_logic (
    id BIGSERIAL PRIMARY KEY,
    theme_name VARCHAR(128) UNIQUE NOT NULL,
    core_thesis TEXT,
    industry_chain JSONB DEFAULT '[]'::jsonb,
    catalysts JSONB DEFAULT '[]'::jsonb,
    monitor_keywords JSONB DEFAULT '[]'::jsonb,
    trigger_rules JSONB DEFAULT '[]'::jsonb,
    invalidation_rules JSONB DEFAULT '[]'::jsonb,
    risks JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS theme_stock_mapping (
    id BIGSERIAL PRIMARY KEY,
    theme_name VARCHAR(128) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    stock_name VARCHAR(128),
    relation TEXT,
    sensitivity_score NUMERIC(10, 4),
    certainty_score NUMERIC(10, 4),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(theme_name, symbol)
);

CREATE TABLE IF NOT EXISTS technical_signal (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(32) NOT NULL,
    signal_date DATE NOT NULL,
    pattern VARCHAR(64) NOT NULL,
    triggered BOOLEAN NOT NULL,
    score NUMERIC(10, 4),
    evidence JSONB DEFAULT '[]'::jsonb,
    risk JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, signal_date, pattern)
);

CREATE TABLE IF NOT EXISTS agent_decision_log (
    id BIGSERIAL PRIMARY KEY,
    decision_date DATE NOT NULL,
    task_type VARCHAR(64),
    user_query TEXT,
    tools_called JSONB,
    input_snapshot JSONB,
    output_summary TEXT,
    suggested_actions JSONB,
    risk_warnings JSONB,
    confidence_score NUMERIC(10, 4),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trade_review (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE,
    symbol VARCHAR(32),
    stock_name VARCHAR(128),
    action VARCHAR(32),
    trade_price NUMERIC(18, 4),
    trade_qty NUMERIC(24, 4),
    reason TEXT,
    matched_strategy VARCHAR(64),
    expected_scenario TEXT,
    invalidation_condition TEXT,
    actual_result TEXT,
    mistake_type VARCHAR(128),
    improvement_note TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mcp_tool_audit_log (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(128),
    user_id VARCHAR(128),
    tool_name VARCHAR(128),
    permission_level VARCHAR(16),
    input_args JSONB,
    output_summary TEXT,
    status VARCHAR(32),
    error_message TEXT,
    latency_ms INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);
