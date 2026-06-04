# ===== AIVIS api service (FastAPI) =====
# Owned by devops. Application source (requirements.txt, app code) is owned by the
# backend agent and is assumed to be filled in later; this Dockerfile only references it.
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

# Dependency layer (cached when requirements.txt is unchanged).
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Application source (filled in by the backend agent).
COPY . .

EXPOSE 8000

# /health endpoint is the contract the compose healthcheck relies on.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
