-- Preserve whether a citation hash was supplied by stock_content or derived
-- from its frozen public quote. Existing historical rows remain readable but
-- cannot be represented as newly verified producer evidence.
ALTER TABLE knowledge_conclusion_citation
  ADD COLUMN quote_hash_provenance TEXT NOT NULL DEFAULT 'LEGACY_UNSPECIFIED';
