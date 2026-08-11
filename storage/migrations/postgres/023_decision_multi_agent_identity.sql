ALTER TABLE investment_decision ADD COLUMN IF NOT EXISTS agent_run_id VARCHAR(36);
ALTER TABLE investment_decision ADD COLUMN IF NOT EXISTS supervisor_version VARCHAR(32);
ALTER TABLE investment_decision ADD COLUMN IF NOT EXISTS participating_agents JSONB;
