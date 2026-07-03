#!/usr/bin/env bash
# ===== AIVIS offline image bundle (save) — CLAUDE.md §3 devops principle =====
# The on-site industrial PC may have NO internet access. Run this on a build
# machine WITH internet to (1) pull base images, (2) build the AIVIS app images,
# (3) save everything into a single tarball you can carry to the site PC.
#
# Usage:
#   ./scripts/offline-save.sh [OUTPUT_TAR] [--gpu]
# Default OUTPUT_TAR = ./aivis-offline-images.tar
#   --gpu  also include the vision GPU image (docker-compose.gpu.yml).
#
# On the site PC, load with scripts/offline-load.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT_TAR="./aivis-offline-images.tar"
USE_GPU=0
for a in "$@"; do
  case "$a" in
    --gpu) USE_GPU=1;;
    *) OUT_TAR="$a";;
  esac
done

# A minimal .env so compose can interpolate variables during build/config.
[ -f .env ] || cp .env.example .env

COMPOSE_FILES=(-f docker-compose.yml)
[ "$USE_GPU" -eq 1 ] && COMPOSE_FILES+=(-f docker-compose.gpu.yml)

echo "[offline-save] building application images..."
docker compose "${COMPOSE_FILES[@]}" build

echo "[offline-save] pulling pinned base images (postgres / minio)..."
docker compose "${COMPOSE_FILES[@]}" pull postgres minio

# Collect every image referenced by the resolved compose config.
IMAGES="$(docker compose "${COMPOSE_FILES[@]}" config --images | sort -u)"
echo "[offline-save] images to bundle:"
echo "$IMAGES" | sed 's/^/    /'

echo "[offline-save] saving -> $OUT_TAR"
# shellcheck disable=SC2086
docker save -o "$OUT_TAR" $IMAGES

echo "[offline-save] done. Transfer these to the site PC:"
echo "    - $OUT_TAR"
echo "    - docker-compose.yml (+ docker-compose.gpu.yml if --gpu)"
echo "    - .env (site values), scripts/offline-load.sh"
ls -lh "$OUT_TAR"
