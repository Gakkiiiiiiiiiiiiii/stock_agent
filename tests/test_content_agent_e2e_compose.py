"""Static safety checks for the isolated Content–Agent compose topology."""
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
TOPOLOGY = ROOT / "deploy" / "e2e" / "content-agent"
COMPOSE = TOPOLOGY / "docker-compose.yml"


def _compose():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_content_agent_topology_is_exactly_knowledge_only():
    services = _compose()["services"]
    assert set(services) == {
        "content-postgres", "content-migrate", "content-api", "content-video-worker", "qdrant",
        "agent-postgres", "agent-migrate", "agent-api", "fixture-model",
    }
    rendered = COMPOSE.read_text(encoding="utf-8").lower()
    assert all(banned not in rendered for banned in ("quant", "stock-factor", "broker", "qmt"))
    assert "${STOCK_CONTENT_CONTEXT:-../../../../stock_content}" in COMPOSE.read_text(encoding="utf-8")
    assert "context: ../../.." in COMPOSE.read_text(encoding="utf-8")


def test_migration_readiness_profiles_and_shared_token_are_explicit():
    services = _compose()["services"]
    real_profiles = {"fixture", "bilibili", "xiaoe", "full-model", "xiaoe-hybrid"}
    for name in ("content-postgres", "content-migrate", "content-api", "qdrant", "agent-postgres", "agent-migrate", "agent-api"):
        assert set(services[name]["profiles"]) == real_profiles
    assert set(services["content-video-worker"]["profiles"]) == real_profiles - {"xiaoe-hybrid"}
    assert services["fixture-model"]["profiles"] == ["fixture"]
    assert services["content-migrate"]["depends_on"]["content-postgres"]["condition"] == "service_healthy"
    assert services["agent-migrate"]["depends_on"]["agent-postgres"]["condition"] == "service_healthy"
    for name in ("content-api", "content-video-worker"):
        assert services[name]["depends_on"]["content-migrate"]["condition"] == "service_completed_successfully"
        assert "qdrant" not in services[name]["depends_on"]
        assert services[name]["environment"]["CONTENT_QDRANT_URL"] == "http://qdrant:6333"
    assert services["agent-api"]["depends_on"]["agent-migrate"]["condition"] == "service_completed_successfully"
    assert services["agent-api"]["depends_on"]["content-api"]["condition"] == "service_healthy"
    assert "fixture-model" not in services["agent-api"].get("depends_on", {})
    assert services["content-api"]["environment"]["CONTENT_SERVICE_API_KEY_FILE"] == "/run/secrets/content-service-api-key"
    assert services["content-api"]["environment"]["CONTENT_SERVICE_API_KEY_PREVIOUS_FILE"] == "/run/secrets/content-service-api-key-previous"
    assert services["agent-api"]["environment"]["CONTENT_SERVICE_API_KEY_FILE"] == "/run/secrets/content-service-api-key"
    assert "content-service-api-key-previous" not in services["agent-api"].get("secrets", [])
    for name in ("content-api", "content-video-worker"):
        environment = services[name]["environment"]
        assert environment["CONTENT_SERVICE_VERSION"] == "${STOCK_CONTENT_SERVICE_VERSION:-0.0.0-candidate}"
        assert environment["CONTENT_GIT_COMMIT"] == "${STOCK_CONTENT_GIT_COMMIT:-ddd677fdb6931f42709a42c3246871bed13d0eef}"
        assert environment["CONTENT_PIPELINE_VERSION"] == "${STOCK_CONTENT_PIPELINE_VERSION:-video-knowledge-pipeline.v1}"
        assert environment["CONTENT_KNOWLEDGE_BUNDLE_CHECKSUM"] == "${CONTENT_KNOWLEDGE_BUNDLE_CHECKSUM:-sha256:EBFD13B78622C3846890438A4FB3CB858278F571FDAB247CDD72EF18CA211621}"
    assert services["agent-api"]["environment"]["AGENT_GIT_COMMIT"] == "${STOCK_AGENT_GIT_COMMIT:-93d4701be24da95b17528872a4f49fdcaebc638e}"
    assert services["agent-api"]["environment"]["CONTENT_KNOWLEDGE_BUNDLE_CHECKSUM"] == "${CONTENT_KNOWLEDGE_BUNDLE_CHECKSUM:-sha256:EBFD13B78622C3846890438A4FB3CB858278F571FDAB247CDD72EF18CA211621}"
    assert "QDRANT" not in " ".join(services["agent-api"]["environment"])
    assert services["content-postgres"]["ports"] == ["127.0.0.1:${CONTENT_POSTGRES_HOST_PORT:-15432}:5432"]
    assert services["agent-postgres"]["ports"] == ["127.0.0.1:${AGENT_POSTGRES_HOST_PORT:-15433}:5432"]
    assert services["content-api"]["ports"] == ["127.0.0.1:${CONTENT_API_HOST_PORT:-18100}:8100"]
    assert services["agent-api"]["ports"] == ["127.0.0.1:${AGENT_API_HOST_PORT:-18000}:8000"]


def test_qdrant_is_an_optional_projection_outside_authoritative_startup_graph():
    services = _compose()["services"]
    # The exact nine-service topology still includes a projection service, but
    # an unavailable projection must not hold up SQL migration, ingestion, the
    # Bundle readiness route, or the Agent research boundary.
    assert "qdrant" in services
    for name in ("content-migrate", "content-api", "content-video-worker", "agent-migrate", "agent-api"):
        assert "qdrant" not in services[name].get("depends_on", {})
    assert "Qdrant is an optional derived-search projection" in (TOPOLOGY / "README.md").read_text(encoding="utf-8")


def test_fixture_stack_is_secret_safe_and_worker_is_confined():
    services = _compose()["services"]
    assert services["agent-api"]["environment"]["STOCK_AGENT_PROFILE"] == "knowledge-only"
    assert services["agent-api"]["environment"]["KNOWLEDGE_CONCLUSION_DETERMINISTIC_FALLBACK"] == "${KNOWLEDGE_CONCLUSION_DETERMINISTIC_FALLBACK:-true}"
    assert services["agent-api"]["environment"]["KNOWLEDGE_CONCLUSION_MODEL_ADAPTER"] == "${KNOWLEDGE_CONCLUSION_MODEL_ADAPTER:-unavailable}"
    assert services["agent-api"]["environment"]["KNOWLEDGE_CONCLUSION_FIXTURE_MODEL_URL"] == "${KNOWLEDGE_CONCLUSION_FIXTURE_MODEL_URL:-}"
    assert services["agent-api"]["environment"]["KNOWLEDGE_CONCLUSION_MODEL_API_KEY_FILE"] == "/run/secrets/agent-model-api-key"
    assert "agent-model-api-key" in services["agent-api"]["secrets"]
    assert "/health/knowledge-bundle-ready" in str(services["content-api"]["healthcheck"])
    assert "/health/knowledge-conclusion-ready" in str(services["agent-api"]["healthcheck"])
    worker = services["content-video-worker"]
    assert worker["user"] == "10001:10001" and worker["read_only"] is True
    assert worker["deploy"]["resources"]["limits"]["pids"] == 128
    assert "no-new-privileges:true" in worker["security_opt"]
    assert any(item.endswith(":ro") for item in worker["volumes"])
    assert "content-media:/data/content/raw" in worker["volumes"]
    assert worker["gpus"] == "all"
    assert worker["environment"]["CONTENT_OCR_DEVICE"] == "gpu:0"
    assert worker["environment"]["CONTENT_OCR_REQUIRE_GPU"] == "true"
    assert worker["environment"]["CONTENT_OCR_HEARTBEAT_FILE"] == "/var/run/stock-content/ocr-heartbeat.json"
    assert services["content-api"]["environment"]["CONTENT_VIDEO_WORKER_HEARTBEAT_FILE"] == "/var/run/stock-content/video-worker-heartbeat.json"
    assert "${CONTENT_OCR_HEARTBEAT_HOST_DIR:-./runtime/ocr}:/var/run/stock-content" in worker["volumes"]
    assert "${CONTENT_OCR_HEARTBEAT_HOST_DIR:-./runtime/ocr}:/var/run/stock-content:ro" in services["content-api"]["volumes"]
    assert (TOPOLOGY / "secrets" / ".gitignore").read_text(encoding="utf-8").splitlines()[0] == "*"
    fixture = (TOPOLOGY / "fixtures" / "fixture_model.py").read_text(encoding="utf-8")
    assert "Never reflect a prompt" in fixture


def test_video_source_credentials_are_file_backed_and_worker_only():
    services = _compose()["services"]
    api = services["content-api"]
    worker = services["content-video-worker"]
    agent = services["agent-api"]

    expected_refs = "${CONTENT_INGESTION_CREDENTIAL_REFS:-bilibili-cookiefile,xiaoe-hls-locator,xiaoe-storage-state}"
    for service in (api, worker):
        environment = service["environment"]
        assert environment["CONTENT_BILIBILI_CREDENTIAL_REF"] == "${CONTENT_BILIBILI_CREDENTIAL_REF:-bilibili-cookiefile}"
        assert environment["CONTENT_XIAOE_HLS_CREDENTIAL_REF"] == "${CONTENT_XIAOE_HLS_CREDENTIAL_REF:-xiaoe-hls-locator}"
        assert environment["CONTENT_XIAOE_CREDENTIAL_REF"] == "${CONTENT_XIAOE_CREDENTIAL_REF:-xiaoe-storage-state}"
        assert environment["CONTENT_INGESTION_CREDENTIAL_REFS"] == expected_refs
        assert environment["CONTENT_INGESTION_CREDENTIAL_PROVIDERS"] == "file-secret"

    assert worker["environment"]["CONTENT_BILIBILI_COOKIEFILE"] == "/run/secrets/bilibili-cookiefile"
    assert worker["environment"]["CONTENT_XIAOE_HLS_LOCATOR_FILE"] == "/run/secrets/xiaoe-hls-locator"
    assert worker["environment"]["CONTENT_XIAOE_STORAGE_STATE_FILE"] == "/run/secrets/xiaoe-storage-state"
    assert {"bilibili-cookiefile", "xiaoe-hls-locator", "xiaoe-storage-state"} <= set(worker["secrets"])
    assert not (set(api.get("secrets", [])) & {"bilibili-cookiefile", "xiaoe-hls-locator", "xiaoe-storage-state"})
    assert not (set(agent.get("secrets", [])) & {"bilibili-cookiefile", "xiaoe-hls-locator", "xiaoe-storage-state"})
    assert "CONTENT_BILIBILI_COOKIEFILE" not in api["environment"]
    assert "CONTENT_XIAOE_HLS_LOCATOR_FILE" not in api["environment"]
    assert "CONTENT_XIAOE_STORAGE_STATE_FILE" not in api["environment"]

    compose = _compose()
    assert compose["secrets"]["bilibili-cookiefile"]["file"] == "${CONTENT_BILIBILI_COOKIEFILE_HOST_FILE:-./fixtures/empty-secret}"
    assert compose["secrets"]["xiaoe-hls-locator"]["file"] == "${CONTENT_XIAOE_HLS_LOCATOR_HOST_FILE:-./fixtures/empty-secret}"
    assert compose["secrets"]["xiaoe-storage-state"]["file"] == "${CONTENT_XIAOE_STORAGE_STATE_HOST_FILE:-./fixtures/empty-secret}"
    rendered = COMPOSE.read_text(encoding="utf-8")
    assert "${BILIBILI_COOKIE_HOST_FILE" not in rendered
    assert "${XIAOE_STORAGE_STATE_HOST_FILE" not in rendered


def test_real_profile_has_no_fixture_model_or_offline_materialization_boundary():
    compose = COMPOSE.read_text(encoding="utf-8")
    services = _compose()["services"]
    assert services["fixture-model"]["profiles"] == ["fixture"]
    assert "fixture-model" not in services["agent-api"].get("depends_on", {})
    assert "${CONTENT_MODEL_URL:?CONTENT_MODEL_URL_REQUIRED}" in compose
    assert "xiaoe-hybrid" in (TOPOLOGY / "README.md").read_text(encoding="utf-8")
    assert "deterministic fallback" in (TOPOLOGY / "README.md").read_text(encoding="utf-8")
