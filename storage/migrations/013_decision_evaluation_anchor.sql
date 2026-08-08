ALTER TABLE investment_decision ADD COLUMN decision_as_of TIMESTAMP;
ALTER TABLE investment_decision ADD COLUMN evaluation_anchor VARCHAR(32) NOT NULL DEFAULT 'NEXT_SESSION_OPEN';
ALTER TABLE investment_decision ADD COLUMN benchmark_symbol VARCHAR(32) NOT NULL DEFAULT '000001.SH';
