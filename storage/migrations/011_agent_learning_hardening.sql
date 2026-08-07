ALTER TABLE market_regime_state ADD COLUMN last_evaluated_date DATE;
ALTER TABLE market_regime_state ADD COLUMN confirmed_days INTEGER NOT NULL DEFAULT 1;
ALTER TABLE investment_decision ADD COLUMN evaluation_status VARCHAR(32) NOT NULL DEFAULT 'PENDING';
ALTER TABLE investment_decision ADD COLUMN next_evaluation_date DATE;
ALTER TABLE investment_decision ADD COLUMN reviewed_at TIMESTAMP;
CREATE INDEX IF NOT EXISTS idx_investment_decision_eval ON investment_decision(evaluation_status, next_evaluation_date);
ALTER TABLE job_task ADD COLUMN not_before TIMESTAMP;
CREATE INDEX IF NOT EXISTS idx_job_task_not_before ON job_task(status, not_before);
