#!/bin/sh
# AIVIS api 컨테이너 시작점(2026-10-15).
# AIVIS_MIGRATE_ON_START=true 면 기동 전에 `alembic upgrade head` 를 돈다 — Render 무료
# 인스턴스는 pre-deploy 명령을 지원하지 않아(유료 전용) 마이그레이션이 영영 안 돌던 문제.
# 마이그레이션이 실패하면 기동하지 않는다(반쯤 맞는 스키마로 뜨는 것보다 낫다).
set -e
if [ "${AIVIS_MIGRATE_ON_START:-false}" = "true" ]; then
  echo "[entrypoint] alembic upgrade head"
  alembic upgrade head
fi
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" ${UVICORN_EXTRA_ARGS:-}
