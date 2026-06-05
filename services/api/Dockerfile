# ===== AIVIS api service (FastAPI) =====
# Owned by devops. Built from the MONOREPO ROOT context (see docker-compose.yml:
# build.context: .) so it can COPY+install the shared schema package
# packages/shared-types/python (aivis_types). Application source under
# services/api/ is owned by the backend agent.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps: curl for the compose healthcheck; libpq for postgres drivers (psycopg).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

# 1) Shared schema package (single source of truth: aivis_types). Installed first
#    so the layer is cached independently of api requirements.
COPY packages/shared-types/python/ /opt/shared-types/python/
RUN pip install /opt/shared-types/python

# 2) api dependency layer (cached when requirements.txt is unchanged).
COPY services/api/requirements.txt ./
RUN pip install -r requirements.txt

# 3) Application source (filled in by the backend agent).
COPY services/api/ .

EXPOSE 8000

# /health endpoint is the contract the compose healthcheck relies on.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
