# ===== AIVIS vision worker (OpenCV + ONNX Runtime, CPU profile) =====
# Owned by devops. Built from the MONOREPO ROOT context (see docker-compose.yml:
# build.context: .) so it can COPY+install the shared schema package
# packages/shared-types/python (aivis_types). For a GPU build use the
# docker-compose.gpu.yml override which targets a CUDA base image.
# Application source under services/vision/ is owned by the vision-ai agent.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AIVIS_CAMERA=sim

WORKDIR /app

# OpenCV runtime system dependencies (libGL, glib) + curl for healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 1) Shared schema package (single source of truth: aivis_types).
COPY packages/shared-types/python/ /opt/shared-types/python/
RUN pip install /opt/shared-types/python

# 2) vision dependency layer (cached when requirements.txt is unchanged).
COPY services/vision/requirements.txt ./
RUN pip install -r requirements.txt

# 3) Application source (filled in by the vision-ai agent).
COPY services/vision/ .

# Liveness probe: the vision worker writes a heartbeat file once its loop is running.
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD test -f /tmp/vision_ready || exit 1

# Default entrypoint runs the inspection worker loop (sim camera by default).
CMD ["python", "-m", "worker"]
