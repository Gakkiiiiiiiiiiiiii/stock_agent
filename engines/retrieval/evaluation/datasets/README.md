# Golden retrieval dataset

`golden_v1.jsonl` should be generated from a versioned, deterministic fixture
corpus—not from production IDs. Each row is validated by `RetrievalGoldenCase`.

The repository intentionally does not ship fabricated expected IDs: those would
produce a misleading CI signal. Build the 200+ curated cases against the test
fixture corpus, record its content/index version, then commit the JSONL together
with the baseline produced by `RetrievalEvaluationRunner`.

Required categories are current market, theme, stock research, recent/historical
video, strategy/decision/preference memory, conflicts, stale knowledge, and
regime-conditioned retrieval.
