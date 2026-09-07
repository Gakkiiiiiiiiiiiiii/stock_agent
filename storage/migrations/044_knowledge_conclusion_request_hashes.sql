-- Separate caller idempotency identity from the effective frozen request.
-- Historical rows predate raw-form retention. Do not fabricate a raw hash:
-- NULL is the explicit unverifiable-legacy marker, while request_hash remains
-- their historical effective identity.
ALTER TABLE knowledge_conclusion_run ADD COLUMN raw_request_json TEXT;
ALTER TABLE knowledge_conclusion_run ADD COLUMN raw_request_hash TEXT;
ALTER TABLE knowledge_conclusion_run ADD COLUMN effective_request_hash TEXT;
UPDATE knowledge_conclusion_run
SET effective_request_hash = request_hash
WHERE effective_request_hash IS NULL;
