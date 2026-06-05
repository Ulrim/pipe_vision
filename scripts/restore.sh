#!/usr/bin/env bash
# ===== AIVIS volume restore (pair of scripts/backup.sh) =====
# Restores PostgreSQL and/or the MinIO image volume from backup artifacts.
#
# Usage:
#   ./scripts/restore.sh --db   backups/db_YYYYMMDD-HHMMSS.sql.gz
#   ./scripts/restore.sh --minio backups/minio_YYYYMMDD-HHMMSS.tar.gz
#   ./scripts/restore.sh --db <db.sql.gz> --minio <minio.tar.gz>
#
# WARNING: restore overwrites current data. Stop dependent services first if needed.
# Run from the repo root.
set -euo pipefail
cd "$(dirname "$0")/.."

DB_FILE=""; MINIO_FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --db)    DB_FILE="$2"; shift 2;;
    --minio) MINIO_FILE="$2"; shift 2;;
    *) echo "unknown arg: $1"; exit 2;;
  esac
done

if [ -z "$DB_FILE" ] && [ -z "$MINIO_FILE" ]; then
  echo "usage: $0 [--db <file.sql.gz>] [--minio <file.tar.gz>]"; exit 2
fi

if [ -f .env ]; then set -a; . ./.env; set +a; fi
PG_USER="${POSTGRES_USER:-aivis}"
PG_DB="${POSTGRES_DB:-aivis}"

# ---- PostgreSQL ----
if [ -n "$DB_FILE" ]; then
  [ -f "$DB_FILE" ] || { echo "db backup not found: $DB_FILE"; exit 1; }
  echo "[restore] postgres <- $DB_FILE"
  echo "[restore] ensuring postgres is up..."
  docker compose up -d postgres
  # Wait for readiness.
  until docker compose exec -T postgres pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; do
    sleep 2; echo "[restore] waiting for postgres..."
  done
  gunzip -c "$DB_FILE" | docker compose exec -T postgres psql -U "$PG_USER" -d "$PG_DB"
  echo "[restore] postgres restore complete."
fi

# ---- MinIO image volume ----
if [ -n "$MINIO_FILE" ]; then
  [ -f "$MINIO_FILE" ] || { echo "minio backup not found: $MINIO_FILE"; exit 1; }
  MINIO_VOL="$(docker volume ls --format '{{.Name}}' | grep -E '_minio-data$' | head -n1 || true)"
  if [ -z "$MINIO_VOL" ]; then
    echo "[restore] minio-data volume not found; creating it via 'docker compose up -d minio'"
    docker compose up -d minio
    MINIO_VOL="$(docker volume ls --format '{{.Name}}' | grep -E '_minio-data$' | head -n1)"
  fi
  echo "[restore] stopping minio to restore volume ($MINIO_VOL)..."
  docker compose stop minio || true
  echo "[restore] minio volume <- $MINIO_FILE"
  docker run --rm -v "${MINIO_VOL}:/data" -v "$(cd "$(dirname "$MINIO_FILE")" && pwd):/backup:ro" alpine \
    sh -c "rm -rf /data/* && tar xzf /backup/$(basename "$MINIO_FILE") -C /data"
  docker compose up -d minio
  echo "[restore] minio restore complete."
fi

echo "[restore] done."
