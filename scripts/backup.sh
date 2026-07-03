#!/usr/bin/env bash
# ===== AIVIS volume backup (CLAUDE.md §3 "헬스체크/자동재시작/볼륨 백업") =====
# Backs up the two stateful stores of the single-host deployment:
#   - PostgreSQL  (inspection results, master data, KPI)  -> SQL dump
#   - MinIO       (raw / result / review images)          -> tar.gz of the volume
#
# Usage:
#   ./scripts/backup.sh [OUTPUT_DIR]
# Default OUTPUT_DIR = ./backups
#
# Run from the repo root (so docker compose finds docker-compose.yml). The DB dump
# uses a running `postgres` service; the image backup tars the named volume directly
# (works even when MinIO is stopped). Restore: see scripts/restore.sh.
set -euo pipefail

cd "$(dirname "$0")/.."

OUT_DIR="${1:-./backups}"
TS="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT_DIR"

# Load .env for POSTGRES_USER / POSTGRES_DB (fall back to compose defaults).
if [ -f .env ]; then set -a; . ./.env; set +a; fi
PG_USER="${POSTGRES_USER:-aivis}"
PG_DB="${POSTGRES_DB:-aivis}"

# Resolve the actual named volume (project prefix = compose project name).
MINIO_VOL="$(docker compose config --volumes 2>/dev/null | grep -x 'minio-data' >/dev/null \
  && docker volume ls --format '{{.Name}}' | grep -E '_minio-data$' | head -n1 || true)"

echo "[backup] output dir : $OUT_DIR"
echo "[backup] timestamp  : $TS"

# ---- 1) PostgreSQL logical dump ----
DB_OUT="$OUT_DIR/db_${TS}.sql.gz"
echo "[backup] postgres -> $DB_OUT"
docker compose exec -T postgres pg_dump -U "$PG_USER" -d "$PG_DB" | gzip > "$DB_OUT"

# ---- 2) MinIO image volume tarball ----
if [ -n "${MINIO_VOL:-}" ]; then
  IMG_OUT="$OUT_DIR/minio_${TS}.tar.gz"
  echo "[backup] minio volume ($MINIO_VOL) -> $IMG_OUT"
  docker run --rm -v "${MINIO_VOL}:/data:ro" -v "$(cd "$OUT_DIR" && pwd):/backup" alpine \
    tar czf "/backup/$(basename "$IMG_OUT")" -C /data .
else
  echo "[backup] WARN: minio-data volume not found (skipping image backup)."
fi

echo "[backup] done. Files in $OUT_DIR:"
ls -lh "$OUT_DIR" | grep -E "_${TS}\." || true
