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

## Dataset schema v2 (`golden_v2_sample.jsonl`)

v2 adds graded labels and query categories while staying backward compatible:
every v1 row still validates (`expected_ids` map onto grade 2). Rows are
validated by `RetrievalGoldenCase`.

Per-row v2 fields:

- `category` — one of `当前市场方向` / `历史主题逻辑` / `个股研究` / `决策经验` /
  `用户偏好` / `视频最新观点` / `冲突知识` / `已过期知识`.
- `graded_labels` — `{doc_id: grade}` per (query, doc):
  - `3` / `"highly_relevant"`, `2` / `"relevant"` — relevant;
  - `1` / `"partially_relevant"`;
  - `0` / `"irrelevant"`;
  - `"expired"` — the doc is expired/superseded knowledge for this query;
  - `"contradictory"` — the doc is the losing side of a contradictory pair.
- `superseded` — `{stale_doc_id: fresher_doc_id}`: a fresher valid version of
  the stale doc exists in the corpus (feeds Temporal Precision).
- `contradictions` — `[{"winner_id": ..., "loser_id": ...}]`: the canonical doc
  and the losing doc of a contradictory pair (feeds Conflict Resolution
  Accuracy).

Metric semantics: Recall@k and MRR count every doc with gain >= 1
(partially_relevant included); nDCG@10 uses the graded gains. v1 binary labels
are uniform gains, so graded nDCG is scale-invariant and reproduces the legacy
binary nDCG values exactly.

The committed sample (`golden_v2_sample.jsonl`) handcrafts one to two cases per
category against the versioned fixture corpus, including expired and
contradictory cases. The full 200–500 real annotated queries are human
annotation work; the schema and loader above are ready for them.
