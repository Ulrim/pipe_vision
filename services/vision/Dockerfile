# ===== AIVIS vision worker (OpenCV + ONNX Runtime, CPU profile) =====
# Owned by devops. Application source (requirements.txt, pipeline code) is owned by the
# vision-ai agent and assumed filled in later. For a GPU build use the
# docker-compose.gpu.yml override (see repo root) which targets a CUDA base image.
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

# Dependency layer (cached when requirements.txt is unchanged).
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Application source (filled in by the vision-ai agent).
COPY . .

# Liveness probe: the vision worker writes a heartbeat file once its loop is running.
# Falls back gracefully until the app implements it.
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD test -f /tmp/vision_ready || exit 1

# Default entrypoint runs the inspection worker loop (sim camera by default).
CMD ["python", "-m", "worker"]
