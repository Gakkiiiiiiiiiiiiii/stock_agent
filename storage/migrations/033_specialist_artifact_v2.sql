ALTER TABLE agent_subtask ADD COLUMN unknowns JSON NOT NULL DEFAULT '[]';
ALTER TABLE agent_subtask ADD COLUMN artifact_hash VARCHAR(64);
