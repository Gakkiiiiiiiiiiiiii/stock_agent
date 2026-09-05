-- Add durable public response and authorization identity for existing 039 installs.
ALTER TABLE decision_runs ADD COLUMN final_response_json TEXT;
ALTER TABLE decision_runs ADD COLUMN final_response_hash TEXT;
ALTER TABLE decision_runs ADD COLUMN lineage_json TEXT;
ALTER TABLE decision_runs ADD COLUMN execution_authorization_json TEXT;
