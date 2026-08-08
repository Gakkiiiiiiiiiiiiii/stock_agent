ALTER TABLE decision_review ADD COLUMN IF NOT EXISTS applicable_regimes JSONB;
ALTER TABLE decision_review ADD COLUMN IF NOT EXISTS invalidation_updates JSONB;
ALTER TABLE decision_review ADD COLUMN IF NOT EXISTS regime_path JSONB;
ALTER TABLE decision_review ADD COLUMN IF NOT EXISTS evidence_refs JSONB;
ALTER TABLE decision_review ADD COLUMN IF NOT EXISTS review_mode VARCHAR(32);
ALTER TABLE decision_review ADD COLUMN IF NOT EXISTS review_model VARCHAR(128);
