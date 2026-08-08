ALTER TABLE decision_review ADD COLUMN applicable_regimes JSON;
ALTER TABLE decision_review ADD COLUMN invalidation_updates JSON;
ALTER TABLE decision_review ADD COLUMN regime_path JSON;
ALTER TABLE decision_review ADD COLUMN evidence_refs JSON;
ALTER TABLE decision_review ADD COLUMN review_mode VARCHAR(32);
ALTER TABLE decision_review ADD COLUMN review_model VARCHAR(128);
