# Content–Agent knowledge-only E2E topology

This Compose file is an isolated `stock_content` + `stock_agent` stack. It
contains only Content, Agent, PostgreSQL, Qdrant, and (for the hermetic
`fixture` profile only) a deterministic fixture model—never Quant, Factor,
Broker, QMT, or an execution path.

## Run selection

Copy `.env.example` to `.env`, set a fresh `COMPOSE_PROJECT_NAME`, then use the
explicit fixture profile:

```powershell
docker compose --env-file .env --profile fixture -f docker-compose.yml config
```

`STOCK_CONTENT_CONTEXT` defaults to the documented sibling-workspace layout
(`../../../../stock_content` from this directory). A prepared worktree may
override it with its own read-only sibling checkout. The Agent build context is
this repository (`../../..`) only.

`STOCK_CONTENT_SERVICE_VERSION`, `STOCK_CONTENT_GIT_COMMIT`,
`STOCK_CONTENT_PIPELINE_VERSION`, and `STOCK_AGENT_GIT_COMMIT` are required
runtime provenance inputs. The example values identify the EPIC-043 functional
commits. The matrix is still candidate while external release gates remain;
production must override these defaults with immutable image/build metadata.

`bilibili`, `xiaoe`, `xiaoe-hybrid`, and `full-model` are opt-in profiles. They must be invoked
explicitly and require their respective read-only host secret file overrides;
no credential value belongs in Compose or `.env`. The fixture token exists only
to exercise the authenticated Content-to-Agent boundary and is not a production
credential. The mounted current Content token is shared by Content and Agent;
the optional previous token is mounted only by Content for rotation.

Public Bilibili uses no credential. For an operator-authorized Bilibili source,
set only `CONTENT_BILIBILI_COOKIEFILE_HOST_FILE` to a read-only Netscape cookie
file and submit the fixed `bilibili-cookiefile` reference. The file is mounted
only into `content-video-worker` at `/run/secrets/bilibili-cookiefile`; neither
the Content API nor Agent receives it.

For a signed Xiaoe HLS source, set only
`CONTENT_XIAOE_HLS_LOCATOR_HOST_FILE` to a read-only current locator file and
submit the fixed `xiaoe-hls-locator` reference with a query-free public HLS
identity. The locator is mounted only into `content-video-worker` at
`/run/secrets/xiaoe-hls-locator`, and is not retained in requests, evidence,
logs, checkpoints, or Compose environment values. For Xiaoe page mode, also
set `CONTENT_XIAOE_PAGE_RESOLVER_ENABLED=true`, an authorized
`CONTENT_XIAOE_PAGE_URL_TEMPLATE`, and
`CONTENT_XIAOE_STORAGE_STATE_HOST_FILE`; that state file is likewise worker
only. These profiles do not bypass login, paywalls, regional restrictions, or
DRM.

## Real Xiaoe E2E and Windows GPU hybrid

`fixture` is the only profile that starts `fixture-model` and permits the
fixture-only ingestion option. A real run must use `xiaoe` (container GPU) or,
on this Windows host, `xiaoe-hybrid`: PostgreSQL and both public APIs stay in
Compose, while the isolated local Content video worker owns Paddle GPU OCR.
Neither profile starts `fixture-model`.

For the hybrid profile, create an operator-only environment file outside this
repository with approved Content model settings, a unique Compose project name,
and paths to the existing private storage-state and OCR runtime. Do not copy a
secret into the environment file.

```powershell
$env:COMPOSE_PROJECT_NAME = "epic043-xiaoe-$PID"
$env:COMPOSE_PROFILES = "xiaoe-hybrid"
$env:STOCK_CONTENT_CONTEXT = "D:/project/worktrees/stock_content-EPIC-043"
$env:CONTENT_MODEL_URL = "https://approved-content-model.example/v1/chat/completions"
$env:CONTENT_MODEL_NAME = "approved-structured-model"
$env:CONTENT_MODEL_API_KEY_HOST_FILE = "C:/operator-secrets/content-model-api-key"
$env:CONTENT_SERVICE_API_KEY_HOST_FILE = "C:/operator-secrets/content-service-current.token"
$env:CONTENT_XIAOE_STORAGE_STATE_HOST_FILE = "C:/operator-secrets/xiaoe-storage-state.json"
$env:CONTENT_XIAOE_PAGE_RESOLVER_ENABLED = "true"
$env:CONTENT_XIAOE_PAGE_URL_TEMPLATE = "https://appaoswidcd4711.h5.xiaoeknow.com/p/course/video/{lesson_id}?product_id={course_id}"
$env:CONTENT_OCR_HEARTBEAT_HOST_DIR = "C:/ProgramData/stock-content/epic043-ocr"
$env:KNOWLEDGE_CONCLUSION_MODEL_ADAPTER = "unavailable"
docker compose --profile xiaoe-hybrid -f docker-compose.yml up -d content-postgres content-migrate content-api agent-postgres agent-migrate agent-api qdrant
```

Then start the worker on the host, not in a container, using its isolated
Paddle/CUDA environment (the API and database ports above are localhost-only):

```powershell
$env:CONTENT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:15432/stock_content"
$env:CONTENT_MODEL_URL = "https://approved-content-model.example/v1/chat/completions"
$env:CONTENT_MODEL_NAME = "approved-structured-model"
$env:CONTENT_MODEL_API_KEY_FILE = "C:/operator-secrets/content-model-api-key"
$env:CONTENT_XIAOE_STORAGE_STATE_FILE = "C:/operator-secrets/xiaoe-storage-state.json"
$env:CONTENT_XIAOE_CREDENTIAL_REF = "xiaoe-storage-state"
$env:CONTENT_VIDEO_WORKER_HEARTBEAT_FILE = "C:/ProgramData/stock-content/epic043-ocr/video-worker-heartbeat.json"
$env:CONTENT_OCR_HEARTBEAT_FILE = "C:/ProgramData/stock-content/epic043-ocr/ocr-heartbeat.json"
$env:CONTENT_OCR_DEVICE = "gpu:0"
$env:CONTENT_OCR_REQUIRE_GPU = "true"
$env:CONTENT_OCR_PYTHON = "C:/path/to/.venv-ocr-cu129/Scripts/python.exe"
$env:CONTENT_OCR_RUNTIME_PATH = "C:/path/to/.venv-ocr-cu129/Scripts;C:/path/to/.venv-ocr-cu129/Library/bin"
$env:CONTENT_OCR_LIBRARY_PATH = "C:/path/to/.venv-ocr-cu129/Library/bin"
& $env:CONTENT_OCR_PYTHON -m stock_content.cli.ocr_doctor
stock-content-worker
```

The worker and API fail readiness if the recorded actual device is not
`gpu:0`; no CPU downgrade is accepted. Keep the OCR runtime path limited to the
Paddle/CUDA environment so Torch DLLs cannot leak into that process.

With the worker healthy, invoke the Agent-owned public-boundary runner. It
submits no transcript, frames, claims, or knowledge and writes aggregate
database evidence only:

```powershell
python scripts/e2e/run_content_agent_flow.py `
  --run-profile real --content-url http://127.0.0.1:18100 --agent-url http://127.0.0.1:18000 `
  --content-token-file C:/operator-secrets/content-service-current.token `
  --source-type xiaoe --source-ref-file C:/operator-secrets/xiaoe-course-ref.txt `
  --credential-ref xiaoe-storage-state --query "提炼投资知识、适用条件和风险" `
  --content-bundle-contract content-knowledge-bundle.v2 `
  --content-database-url postgresql+psycopg://postgres:postgres@127.0.0.1:15432/stock_content `
  --agent-database-url postgresql+psycopg://agent:agent@127.0.0.1:15433/stock_agent `
  --evidence-dir D:/project/integration/runs/EPIC-043/xiaoe-real-http-e2e
```

The runner proves duplicate and conflicting idempotency behavior for ingestion,
Bundle, and conclusion; immutable Bundle GET readback; conclusion GET/lineage;
both replay modes; and aggregate Content/Agent PostgreSQL readback. A successful
Agent result still reports `model.mode=FALLBACK` until a reviewed Agent model
adapter exists. That is an analysis-only conclusion, never an execution signal.

## Ordering and readiness

Each database must pass `pg_isready`, then its one-shot migration owner must
finish successfully before its API or worker starts. APIs verify schemas; they
do not mutate them. `agent-api` also has a runtime readiness probe for Content's
`/health/knowledge-bundle-ready`, its locked checksum
`sha256:EBFD13B78622C3846890438A4FB3CB858278F571FDAB247CDD72EF18CA211621`,
and a model or deterministic fallback. Compose dependencies shorten startup;
the Agent route remains the source of truth for a Content outage or checksum
mismatch.

The Agent real composition injects an explicit unavailable-model adapter, so
its readiness reports `model=degraded` even if an environment flag is set.
It remains ready only when deterministic fallback is enabled.  No arbitrary
provider URL or credentials are interpreted by this topology; installing a
real structured-model adapter is a separate, credentialed deployment change.

Qdrant is an optional derived-search projection, not a startup or health
dependency for `content-api` or `content-video-worker`. Those services keep its
endpoint configured so projection/reconciliation work can resume after Qdrant
recovers, while their SQL-backed Bundle route continues to serve ingestion and
research. `agent-api` never queries Qdrant: its readiness gates only the
Content Bundle route and its checksum. A degraded projection must be observed
and repaired separately; it does not turn an authoritative SQL Bundle into an
unhealthy one. `fixture-model` is a local-only deterministic HTTP endpoint with
a fixed research-only response; it does not produce executable or trading
language.

The video worker keeps SC-11 isolation: non-root, read-only root filesystem,
tmpfs work areas, resource/PID limits, read-only fixtures and secret mounts,
and a dedicated media volume. Docker Compose alone cannot enforce its upstream
domain egress policy; authorized Bilibili/Xiaoe/full-model environments must
apply the Content repository's `docker/video-worker-egress-policy.yaml` through
their proxy/CNI owner. The media volume is never a CI artifact.

## Migration rehearsal

For an N-1 rehearsal, preserve a named prior project-volume namespace, start
only the two migration services from the current checkout against copies of
those volumes, then run both readiness routes. Do not claim an N-1 pass without
an identified prior image/volume and its recorded migration ledger; this packet
does not manufacture an old volume image.
