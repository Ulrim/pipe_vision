#!/usr/bin/env bash
# ===== AIVIS offline image bundle (load) — run on the on-site (offline) PC =====
# Loads the image tarball produced by scripts/offline-save.sh, then brings the
# full stack up with `docker compose up -d` (no network pulls needed).
#
# Usage:
#   ./scripts/offline-load.sh [INPUT_TAR] [--gpu] [--no-up]
# Default INPUT_TAR = ./aivis-offline-images.tar
#   --gpu     layer docker-compose.gpu.yml on startup (CUDA vision worker).
#   --no-up   only load images, do not start the stack.
set -euo pipefail
cd "$(dirname "$0")/.."

IN_TAR="./aivis-offline-images.tar"
USE_GPU=0; DO_UP=1
for a in "$@"; do
  case "$a" in
    --gpu)    USE_GPU=1;;
    --no-up)  DO_UP=0;;
    *)        IN_TAR="$a";;
  esac
done

[ -f "$IN_TAR" ] || { echo "image tarball not found: $IN_TAR"; exit 1; }
[ -f .env ] || { echo "ERROR: .env missing. Copy .env.example to .env and set site values."; exit 1; }

echo "[offline-load] loading images from $IN_TAR ..."
docker load -i "$IN_TAR"

if [ "$DO_UP" -eq 1 ]; then
  COMPOSE_FILES=(-f docker-compose.yml)
  [ "$USE_GPU" -eq 1 ] && COMPOSE_FILES+=(-f docker-compose.gpu.yml)
  echo "[offline-load] starting stack..."
  docker compose "${COMPOSE_FILES[@]}" up -d
  echo "[offline-load] status:"
  docker compose "${COMPOSE_FILES[@]}" ps
else
  echo "[offline-load] images loaded. Start later with: docker compose up -d"
fi
