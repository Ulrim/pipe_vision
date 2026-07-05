#!/usr/bin/env bash
# =============================================================================
# AIVIS 독립형(모드 B) 런처 — 라즈베리파이 1대에서 api(sqlite) + 검사 워커(picam)
# 를 함께 기동한다. 클라우드/네트워크 불필요. 데이터는 로컬(sqlite + 파일)에 저장.
# 화면(HMI)은 scripts/serve-spa.py 로 별도 서빙한다(docs/RASPBERRY_PI.md 참조).
#
# 사용:
#   bash scripts/aivis-standalone.sh          # 포그라운드 실행, Ctrl+C 로 전체 종료
# 주요 환경변수(선택, 기본값 있음):
#   AIVIS_HOME=/var/lib/aivis     데이터 루트(db/images/spool)
#   API_PORT=8000                 api 포트
#   AIVIS_CAMERA=picam            picam(실카메라) | sim(무카메라 테스트)
#   AIVIS_ITEM_CODE=HP12          검사 품목
#   AIVIS_ADMIN_PASSWORD=...      시드 admin 비번(첫 로그인용)
#   JWT_SECRET=...                토큰 서명키(재시작 간 고정 권장)
#   AIVIS_API_VENV / AIVIS_WORKER_VENV   각 python 실행경로 오버라이드(고급)
# =============================================================================
set -euo pipefail

# --- 저장소 루트(스크립트 위치 기준) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- 설정(환경변수 오버라이드 가능) ---
AIVIS_HOME="${AIVIS_HOME:-/var/lib/aivis}"
API_PORT="${API_PORT:-8000}"
AIVIS_CAMERA="${AIVIS_CAMERA:-picam}"
AIVIS_ITEM_CODE="${AIVIS_ITEM_CODE:-HP12}"
AIVIS_ADMIN_PASSWORD="${AIVIS_ADMIN_PASSWORD:-aivis1234}"
JWT_SECRET="${JWT_SECRET:-aivis-standalone-secret-change-me}"
AIVIS_WORKER_INTERVAL_MS="${AIVIS_WORKER_INTERVAL_MS:-1500}"

IMAGES_DIR="$AIVIS_HOME/images"
SPOOL_DIR="$AIVIS_HOME/spool"
DB_PATH="$AIVIS_HOME/db/aivis.db"

# venv python 경로(오버라이드 가능). 기본: api 는 전용 venv, worker 는
# --system-site-packages venv(picamera2/cv2 상속, docs STEP 3 에서 생성).
API_VENV="${AIVIS_API_VENV:-$REPO/services/api/.venv-api/bin/python}"
WORKER_VENV="${AIVIS_WORKER_VENV:-$REPO/services/vision/.venv/bin/python}"

log() { echo "[standalone] $*"; }

mkdir -p "$IMAGES_DIR" "$SPOOL_DIR" "$(dirname "$DB_PATH")"

# --- api venv 준비(없으면 생성 후 의존 설치) ---
if [ ! -x "$API_VENV" ] && [ -z "${AIVIS_API_VENV:-}" ]; then
  log "api venv 생성: $REPO/services/api/.venv-api"
  python3 -m venv "$REPO/services/api/.venv-api"
  "$REPO/services/api/.venv-api/bin/pip" install -q --upgrade pip
  "$REPO/services/api/.venv-api/bin/pip" install -q -r "$REPO/services/api/requirements.txt"
  "$REPO/services/api/.venv-api/bin/pip" install -q -e "$REPO/packages/shared-types/python"
fi
[ -x "$API_VENV" ] || { log "ERROR: api python 없음: $API_VENV"; exit 1; }
[ -x "$WORKER_VENV" ] || { log "ERROR: worker python 없음: $WORKER_VENV (docs STEP 3 의 --system-site-packages venv 를 만들어라)"; exit 1; }

# --- 공용 env ---
export AIVIS_STORAGE_BACKEND=local
export AIVIS_IMAGES_DIR="$IMAGES_DIR"
export JWT_SECRET
export AIVIS_SEED_ADMIN_USER=admin
export AIVIS_SEED_ADMIN_PASSWORD="$AIVIS_ADMIN_PASSWORD"

API_PID=""
cleanup() {
  log "종료 중…"
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --- 1) api (sqlite, 시드 admin + 데모 품목) ---
log "api 기동 :$API_PORT (sqlite=$DB_PATH, images=$IMAGES_DIR)"
(
  cd "$REPO/services/api"
  DATABASE_URL="sqlite:///$DB_PATH" \
  AIVIS_SEED_ON_STARTUP=true \
  AIVIS_SEED_DEMO_ITEM=true \
  AIVIS_DEMO_ITEM_CODE="$AIVIS_ITEM_CODE" \
  exec "$API_VENV" -m uvicorn main:app --host 0.0.0.0 --port "$API_PORT" --log-level info
) &
API_PID=$!

# --- api health 대기 ---
log "api health 대기…"
for i in $(seq 1 40); do
  if "$WORKER_VENV" - "$API_PORT" <<'PY' 2>/dev/null
import sys, urllib.request
try:
    urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/health", timeout=2)
except Exception:
    sys.exit(1)
PY
  then log "api ready"; break; fi
  sleep 1
  [ "$i" = 40 ] && { log "ERROR: api 기동 실패"; exit 1; }
done

# --- 2) 검사 워커 (picam → localhost api, 로컬 저장 + 오프라인 스풀) ---
log "워커 기동 (camera=$AIVIS_CAMERA, item=$AIVIS_ITEM_CODE)"
cd "$REPO/services/vision"
AIVIS_CAMERA="$AIVIS_CAMERA" \
AIVIS_API_URL="http://127.0.0.1:$API_PORT" \
AIVIS_ITEM_CODE="$AIVIS_ITEM_CODE" \
AIVIS_CAM_ID="${AIVIS_CAM_ID:-PI-CAM1}" \
AIVIS_WORKER_INTERVAL_MS="$AIVIS_WORKER_INTERVAL_MS" \
AIVIS_SPOOL_DIR="$SPOOL_DIR" \
exec "$WORKER_VENV" -m worker
