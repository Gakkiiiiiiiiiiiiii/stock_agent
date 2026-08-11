ALTER TABLE investment_decision ADD COLUMN agent_run_id VARCHAR(36);
ALTER TABLE investment_decision ADD COLUMN supervisor_version VARCHAR(32);
ALTER TABLE investment_decision ADD COLUMN participating_agents JSON;
