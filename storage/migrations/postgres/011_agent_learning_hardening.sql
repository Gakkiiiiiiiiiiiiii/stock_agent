ALTER TABLE market_regime_state ADD COLUMN IF NOT EXISTS last_evaluated_date DATE;
ALTER TABLE market_regime_state ADD COLUMN IF NOT EXISTS confirmed_days INTEGER NOT NULL DEFAULT 1;
ALTER TABLE investment_decision ADD COLUMN IF NOT EXISTS evaluation_status VARCHAR(32) NOT NULL DEFAULT 'PENDING';
ALTER TABLE investment_decision ADD COLUMN IF NOT EXISTS next_evaluation_date DATE;
ALTER TABLE investment_decision ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_investment_decision_eval ON investment_decision(evaluation_status, next_evaluation_date);
ALTER TABLE job_task ADD COLUMN IF NOT EXISTS not_before TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_job_task_not_before ON job_task(status, not_before);
