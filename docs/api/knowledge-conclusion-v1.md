# Knowledge Conclusion v1

`knowledge-only` exposes only immutable, content-bound research conclusions.

- `POST /api/v2/knowledge-conclusions` requires `Content-Type: application/json` and `Idempotency-Key`. It obtains the locked Content knowledge bundle, validates and freezes it before synthesis, and returns `knowledge-conclusion.v1` with `scope=CONTENT_ONLY_RESEARCH` and `execution_eligible=false`.
- `GET /api/v2/knowledge-conclusions/{conclusion_id}` returns the stored immutable result.
- `POST /api/v2/knowledge-conclusions/{conclusion_id}/replay` accepts `VERIFY_HASH`, `RECOMPUTE_DETERMINISTIC`, or `RECOMPUTE_MODEL`. Replay reads frozen persistence only; model replay requires a new idempotent run.
- `GET /api/v2/knowledge-conclusions/{conclusion_id}/lineage` reconstructs frozen SA-09 lineage only.

Errors use `{code,message,trace_id,retryable}` and do not return upstream bodies, URLs, or credentials. `STOCK_AGENT_API_AUTH_MODE=none|bearer` controls authorization. Bearer tokens are read only from `STOCK_AGENT_API_AUTH_TOKEN_FILE` and optional `STOCK_AGENT_API_AUTH_PREVIOUS_TOKEN_FILE`.

`GET /health/knowledge-conclusion-ready` is ready only when the knowledge-only profile, migrations 042/043, conclusion repository, Content readiness and checksum lock, an available model or enabled deterministic fallback, and UTC-aware clock pass. It never probes Quant, Factor, Broker, or execution dependencies.

The `fixture` model adapter is an isolated fixed-route test binding. Its health
probe requires both its file-backed token and the fixture protocol identity;
an environment flag cannot claim model readiness. A real model remains
unavailable until an approved provider adapter is installed. Migration 044
keeps pre-raw-request rows explicitly unverifiable (`raw_request_* = NULL`):
they must not be replayed or reused for idempotency as though their effective
request were the original caller request.
