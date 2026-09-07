FROM python:3.11-slim AS api-deps

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir --prefix=/install \
    "sqlalchemy>=2.0" "pydantic>=2.7" "pyyaml>=6.0" \
    "fastapi>=0.111" "httpx>=0.27" "uvicorn>=0.30" \
    "filelock>=3.15" "psycopg[binary]>=3.2" "redis>=5.0" \
    "anthropic>=0.67" "pandas>=2.2" "qdrant-client>=1.11" \
    "rich>=13.0" "typer>=0.9"

FROM python:3.11-slim AS worker-deps
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir --prefix=/install \
    "sqlalchemy>=2.0" "pydantic>=2.7" "pyyaml>=6.0" \
    "filelock>=3.15" "psycopg[binary]>=3.2" "redis>=5.0" \
    "httpx>=0.27" "qdrant-client>=1.11"

FROM python:3.11-slim AS analysis-deps
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir --prefix=/install --extra-index-url https://download.pytorch.org/whl/cpu \
    "sqlalchemy>=2.0" "pydantic>=2.7" "pyyaml>=6.0" \
    "fastapi>=0.111" "httpx>=0.27" "uvicorn>=0.30" \
    "anthropic>=0.67" "pandas>=2.2" "numpy<2" "qdrant-client>=1.11" \
    "sentence-transformers>=3.0,<4.0" "torch==2.2.2+cpu" "rich>=13.0" "typer>=0.9"

# API role: decision authority plus read-only analysis routes.
FROM python:3.11-slim AS api
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY --from=api-deps /install /usr/local
COPY pyproject.toml README.md ./
COPY app ./app
COPY agent ./agent
COPY clients ./clients
COPY config ./config
COPY contracts ./contracts
COPY financial_agent ./financial_agent
COPY knowledge_base ./knowledge_base
COPY skills ./skills
COPY engines/__init__.py engines/domain_result.py engines/versioning.py ./engines/
COPY engines/advisory ./engines/advisory
COPY engines/decision ./engines/decision
COPY engines/market ./engines/market
COPY engines/memory ./engines/memory
COPY engines/opportunity ./engines/opportunity
COPY engines/policy ./engines/policy
COPY engines/portfolio ./engines/portfolio
COPY engines/retrieval ./engines/retrieval
COPY engines/risk ./engines/risk
COPY services/__init__.py services/subsystems.py ./services/
COPY services/evidence ./services/evidence
COPY storage/__init__.py storage/bootstrap.py storage/db.py ./storage/
COPY storage/models ./storage/models
COPY storage/repositories ./storage/repositories
# API verifies a schema prepared by the explicit migration-owner target. Keep
# the legacy formal allowlist available to existing FULL runtime tooling only;
# the API command itself never executes it.
COPY storage/migrations/039_decision_unit_of_work.sql \
     storage/migrations/040_replay_outcome_runs.sql \
     storage/migrations/041_decision_run_final_response.sql \
     ./storage/migrations/
EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]

# Explicit database schema owner. Deploy this target as a one-shot job before
# an API rollout; it is never the API entrypoint.
FROM api AS migration-owner
COPY storage/migrations ./storage/migrations
COPY scripts/migrate_schema.py ./scripts/migrate_schema.py
CMD ["python", "scripts/migrate_schema.py"]

# Worker role: durable job handlers and retrieval-evaluation dependencies.
FROM python:3.11-slim AS worker
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY --from=worker-deps /install /usr/local
COPY pyproject.toml README.md ./
COPY app/__init__.py ./app/
COPY app/application/__init__.py ./app/application/
COPY app/application/outcomes ./app/application/outcomes
COPY clients ./clients
COPY config ./config
COPY contracts ./contracts
COPY financial_agent ./financial_agent
COPY engines/__init__.py engines/domain_result.py ./engines/
COPY engines/decision ./engines/decision
COPY engines/market/trading_clock.py ./engines/market/
COPY engines/memory ./engines/memory
COPY engines/retrieval ./engines/retrieval
COPY storage/__init__.py storage/bootstrap.py storage/db.py ./storage/
COPY storage/models ./storage/models
COPY storage/repositories ./storage/repositories
COPY workers/__init__.py workers/job_types.py workers/job_worker.py workers/outcome_worker.py workers/vector_index_worker.py ./workers/
CMD ["python", "-m", "workers.job_worker"]

# Analysis role: DecisionRuntime plus the local embedding/reranking adapters.
FROM python:3.11-slim AS analysis
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY --from=analysis-deps /install /usr/local
COPY pyproject.toml README.md ./
COPY app ./app
COPY agent ./agent
COPY clients ./clients
COPY config ./config
COPY contracts ./contracts
COPY financial_agent ./financial_agent
COPY knowledge_base ./knowledge_base
COPY skills ./skills
COPY engines/__init__.py engines/domain_result.py engines/versioning.py ./engines/
COPY engines/advisory ./engines/advisory
COPY engines/decision ./engines/decision
COPY engines/market ./engines/market
COPY engines/memory ./engines/memory
COPY engines/opportunity ./engines/opportunity
COPY engines/policy ./engines/policy
COPY engines/portfolio ./engines/portfolio
COPY engines/retrieval ./engines/retrieval
COPY engines/risk ./engines/risk
COPY services/__init__.py services/subsystems.py ./services/
COPY services/evidence ./services/evidence
COPY storage/__init__.py storage/bootstrap.py storage/db.py ./storage/
COPY storage/models ./storage/models
COPY storage/repositories ./storage/repositories
COPY workers/__init__.py workers/embedding_api.py workers/reranker_api.py ./workers/
CMD ["python", "-c", "from app.decision_runtime import DecisionRuntime; print('analysis runtime ready')"]

FROM api AS runtime
