#!/usr/bin/env bash
# =============================================================================
# AIVIS 독립형(모드 B) 런처 — 라즈베리파이/단일 호스트 1대에서 전체 스택 원클릭 기동:
#   api(sqlite) + 검사 워커(picam|sim) + HMI 화면(:5173) + 관리자 대시보드(:5174)
# 클라우드/네트워크 불필요. 데이터는 로컬(sqlite + 파일)에 저장.
# 화면 서빙은 scripts/serve-spa.py(표준 라이브러리 SPA 폴백 서버)를 사용한다.
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
#   AIVIS_SERVE_HMI=true          HMI 정적 서빙(apps/hmi/dist) on/off
#   AIVIS_SERVE_DASHBOARD=true    대시보드 정적 서빙(apps/dashboard/dist) on/off
#   HMI_PORT=5173 / DASHBOARD_PORT=5174   화면 포트
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
AIVIS_SERVE_HMI="${AIVIS_SERVE_HMI:-true}"
AIVIS_SERVE_DASHBOARD="${AIVIS_SERVE_DASHBOARD:-true}"
HMI_PORT="${HMI_PORT:-5173}"
DASHBOARD_PORT="${DASHBOARD_PORT:-5174}"

IMAGES_DIR="$AIVIS_HOME/images"
SPOOL_DIR="$AIVIS_HOME/spool"
DB_PATH="$AIVIS_HOME/db/aivis.db"

# venv python 경로(오버라이드 가능). 기본: api 는 전용 venv, worker 는
# --system-site-packages venv(picamera2/cv2 상속, docs STEP 3 에서 생성).
API_VENV="${AIVIS_API_VENV:-$REPO/services/api/.venv-api/bin/python}"
WORKER_VENV="${AIVIS_WORKER_VENV:-$REPO/services/vision/.venv/bin/python}"

log() { echo "[standalone] $*"; }

mkdir -p "$IMAGES_DIR" "$SPOOL_DIR" "$(dirname "$DB_PATH")"

# --- api venv 준비 ---------------------------------------------------------
# 버그수정: 과거엔 .venv-api/bin/python "존재"만 확인해서, venv 는 생겼는데
# pip 설치가 실패/중단된 경우("No module named uvicorn") 그대로 기동 실패했다.
# → venv 가 있어도 핵심 모듈 import 검사에 실패하면 의존 설치를 (재)실행한다.
api_deps_ok() {
  "$API_VENV" -c "import uvicorn, fastapi, sqlalchemy, aivis_types" >/dev/null 2>&1
}

install_api_deps() {
  # 설치 로그는 숨기지 않는다(-q 금지) — 실패 지점을 현장에서 바로 볼 수 있게.
  log "api 의존 설치 (재)실행 중… (파이에서 수 분 걸릴 수 있음, 로그 그대로 출력)"
  "$API_VENV" -m pip install --upgrade pip
  "$API_VENV" -m pip install -r "$REPO/services/api/requirements.txt"
  "$API_VENV" -m pip install -e "$REPO/packages/shared-types/python"
}

if [ ! -x "$API_VENV" ]; then
  if [ -n "${AIVIS_API_VENV:-}" ]; then
    log "ERROR: api python 없음: $API_VENV (AIVIS_API_VENV 경로를 확인하라)"
    exit 1
  fi
  log "api venv 생성: $REPO/services/api/.venv-api"
  python3 -m venv "$REPO/services/api/.venv-api"
fi
if ! api_deps_ok; then
  log "api venv 의존 미비 감지(uvicorn/fastapi/sqlalchemy/aivis_types import 실패) → 설치 시작"
  install_api_deps || {
    log "ERROR: api 의존 설치 실패 — 위 pip 로그와 네트워크(오프라인이면 휠 미러) 확인"
    exit 1
  }
  api_deps_ok || {
    log "ERROR: 설치 후에도 import 실패 — venv 재생성 권장: rm -rf $REPO/services/api/.venv-api 후 재실행"
    exit 1
  }
fi
[ -x "$WORKER_VENV" ] || { log "ERROR: worker python 없음: $WORKER_VENV (docs STEP 3 의 --system-site-packages venv 를 만들어라)"; exit 1; }

# --- sqlite 스키마 구버전 감지(다중모드 컬럼) --------------------------------
# 구버전 런처로 만든 DB 에는 inspection.tube_index / expected_count(다중 튜브
# 배치검사) 컬럼이 없어 api 가 500 을 낸다. 자동 삭제는 하지 않는다(데이터 보호).
if [ -f "$DB_PATH" ]; then
  if ! python3 - "$DB_PATH" <<'PY'
import sqlite3, sys
cols = [r[1] for r in sqlite3.connect(sys.argv[1]).execute(
    "PRAGMA table_info(inspection)")]
# 테이블이 아직 없으면(빈 파일 등) api 가 새로 만드므로 통과.
sys.exit(1 if (cols and "tube_index" not in cols) else 0)
PY
  then
    log "ERROR: 구버전 DB 스키마 감지 — $DB_PATH 의 inspection 테이블에 tube_index(다중모드) 컬럼이 없다."
    log "  · 데이터 보존이 필요 없으면:  rm '$DB_PATH'  후 재실행(새로 생성됨)"
    log "  · 보존이 필요하면: cd $REPO/services/api && DATABASE_URL='sqlite:///$DB_PATH' $API_VENV -m alembic upgrade head"
    exit 1
  fi
fi

# --- 공용 env ---
export AIVIS_STORAGE_BACKEND=local
export AIVIS_IMAGES_DIR="$IMAGES_DIR"
export JWT_SECRET
export AIVIS_SEED_ADMIN_USER=admin
export AIVIS_SEED_ADMIN_PASSWORD="$AIVIS_ADMIN_PASSWORD"

# --- 프로세스 수명주기: Ctrl+C(EXIT/INT/TERM) 시 전 프로세스 정리 ---
PIDS=()
cleanup() {
  trap - EXIT INT TERM
  log "종료 중… (api/워커/화면 서버 정리)"
  local pid
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  log "전체 종료 완료"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

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
PIDS+=("$API_PID")

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

# --- 2) 화면 서빙(HMI/대시보드) — serve-spa.py 백그라운드 ---
HMI_URL=""
DASHBOARD_URL=""
start_spa() { # $1=라벨 $2=dist경로 $3=포트 $4=npm 워크스페이스
  if [ ! -f "$2/index.html" ]; then
    log "WARN: $1 빌드 산출물 없음($2/index.html) → 서빙 건너뜀."
    log "      빌드하려면: npm run build --workspace $4  (이후 재실행)"
    return 1
  fi
  python3 "$SCRIPT_DIR/serve-spa.py" "$2" "$3" &
  PIDS+=("$!")
  log "$1 서빙 :$3 (pid $!)"
  return 0
}
if [ "$AIVIS_SERVE_HMI" = "true" ]; then
  if start_spa "HMI" "$REPO/apps/hmi/dist" "$HMI_PORT" "@aivis/hmi"; then
    HMI_URL="http://localhost:$HMI_PORT"
  fi
fi
if [ "$AIVIS_SERVE_DASHBOARD" = "true" ]; then
  if start_spa "대시보드" "$REPO/apps/dashboard/dist" "$DASHBOARD_PORT" "@aivis/dashboard"; then
    DASHBOARD_URL="http://localhost:$DASHBOARD_PORT"
  fi
fi

# --- 3) 검사 워커 (picam|sim → localhost api, 로컬 저장 + 오프라인 스풀) ---
log "워커 기동 (camera=$AIVIS_CAMERA, item=$AIVIS_ITEM_CODE)"
(
  cd "$REPO/services/vision"
  AIVIS_CAMERA="$AIVIS_CAMERA" \
  AIVIS_API_URL="http://127.0.0.1:$API_PORT" \
  AIVIS_ITEM_CODE="$AIVIS_ITEM_CODE" \
  AIVIS_CAM_ID="${AIVIS_CAM_ID:-PI-CAM1}" \
  AIVIS_WORKER_INTERVAL_MS="$AIVIS_WORKER_INTERVAL_MS" \
  AIVIS_SPOOL_DIR="$SPOOL_DIR" \
  exec "$WORKER_VENV" -m worker
) &
WORKER_PID=$!
PIDS+=("$WORKER_PID")

# --- 기동 요약 ---
log "─────────────────────────────────────────────────────"
log "AIVIS 독립형 기동 완료 (Ctrl+C 로 전체 종료)"
log "  API      → http://localhost:$API_PORT"
[ -n "$HMI_URL" ]       && log "  HMI      → $HMI_URL"
[ -n "$DASHBOARD_URL" ] && log "  대시보드  → $DASHBOARD_URL"
log "  로그인    admin/$AIVIS_ADMIN_PASSWORD"
log "─────────────────────────────────────────────────────"

# 워커를 앵커로 대기 — 워커 종료(오류/AIVIS_WORKER_MAX_ITER) 시 전체 정리.
wait "$WORKER_PID"
