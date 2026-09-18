#!/usr/bin/env bash
# =============================================================================
# AIVIS 라즈베리파이 통합 설치 — 이 명령 하나로 설치부터 자동실행 등록까지
#
#   bash scripts/aivis-install.sh
#
# 하는 일(순서대로):
#   [1/6] 준비 확인 — 시스템/카메라/디스크
#   [2/6] 시스템 패키지 설치 (git, python3, picamera2, opencv, nodejs)
#   [3/6] 파이썬 환경 만들기 (검사 워커용 / API 용)
#   [4/6] 화면 만들기 (작업자 화면 + 관리자 대시보드)
#   [5/6] 부팅 자동실행 등록 — 전원만 켜면 검사가 시작됩니다
#   [6/6] 시작 + 접속 주소 안내
#
# 옵션:
#   --no-service   부팅 자동실행을 등록하지 않는다(수동 실행만)
#   --no-build     화면 빌드를 건너뛴다(이미 만들어 둔 경우)
#   --user NAME    서비스를 실행할 계정(기본: 지금 로그인한 계정)
#   -h, --help     이 도움말
#
# 이 스크립트는 **일반 계정으로** 실행하세요(sudo 붙이지 마세요).
# 관리자 권한이 필요한 부분에서만 알아서 sudo 를 씁니다(비밀번호를 한 번 물어봅니다).
# 여러 번 실행해도 안전합니다(이미 된 단계는 건너뜁니다).
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

DO_SERVICE=true
DO_BUILD=true
RUN_USER=""

C_B=$'\033[1m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_0=$'\033[0m'
say()  { echo "$*"; }
step() { echo; echo "${C_B}[$1] $2${C_0}"; }
ok()   { echo "    ${C_G}[완료]${C_0} $*"; }
info() { echo "    $*"; }
warn() { echo "    ${C_Y}[주의]${C_0} $*"; }
die()  {
  echo >&2
  echo >&2 "${C_R}[실패]${C_0} $*"
  echo >&2
  echo >&2 "  이 메시지를 그대로 알려주시면 원인을 확인할 수 있습니다."
  exit 1
}

usage() { sed -n '3,/^# =\{10,\}/p' "${BASH_SOURCE[0]}" | sed 's/^#\( \|$\)//;s/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --no-service) DO_SERVICE=false ;;
    --no-build)   DO_BUILD=false ;;
    --user)       RUN_USER="${2:-}"; shift ;;
    -h|--help)    usage; exit 0 ;;
    *) die "알 수 없는 옵션: $1  (도움말: bash scripts/aivis-install.sh --help)" ;;
  esac
  shift
done

[ -z "$RUN_USER" ] && RUN_USER="$(id -un)"

# sudo 사용 방식 결정(루트로 실행 중이면 sudo 불필요).
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
  warn "root 로 실행 중입니다. 일반 계정으로 실행하는 편이 안전합니다."
else
  command -v sudo >/dev/null 2>&1 || die "sudo 가 없습니다. 관리자에게 문의하세요."
  SUDO="sudo"
fi

echo "==============================================================="
echo "  ${C_B}AIVIS 설치${C_0}"
echo "  저장소 : $REPO"
echo "  계정   : $RUN_USER"
echo "  자동실행 등록 : $($DO_SERVICE && echo 예 || echo 아니오)"
echo "==============================================================="

# =============================================================================
# [1/6] 준비 확인
# =============================================================================
step "1/6" "준비 확인"

if [ -r /etc/os-release ]; then
  . /etc/os-release
  info "운영체제 : ${PRETTY_NAME:-알 수 없음}"
fi
ARCH="$(uname -m)"
info "아키텍처 : $ARCH"
case "$ARCH" in
  aarch64|arm64) : ;;
  *) warn "라즈베리파이(64비트)가 아닌 환경으로 보입니다. 설치는 진행하지만 카메라가 동작하지 않을 수 있습니다." ;;
esac

# 디스크 여유(빌드에 넉넉히 필요).
AVAIL_MB="$(df -Pm "$REPO" 2>/dev/null | awk 'NR==2{print $4}')"
if [ -n "${AVAIL_MB:-}" ]; then
  info "남은 디스크 : ${AVAIL_MB}MB"
  [ "$AVAIL_MB" -lt 2000 ] && warn "여유 공간이 2GB 미만입니다. 빌드 중 실패할 수 있습니다."
fi

# 카메라(설치 자체는 막지 않는다 — 나중에 연결해도 된다).
if command -v rpicam-hello >/dev/null 2>&1 || command -v libcamera-hello >/dev/null 2>&1; then
  CAM_CMD="$(command -v rpicam-hello || command -v libcamera-hello)"
  if "$CAM_CMD" --list-cameras 2>/dev/null | grep -qi "imx\|Available"; then
    ok "카메라 인식됨"
  else
    warn "카메라가 인식되지 않습니다. 리본 케이블 방향/체결을 확인하세요(나중에 확인해도 됩니다)."
  fi
fi
ok "준비 확인 완료"

# =============================================================================
# [2/6] 시스템 패키지
# =============================================================================
step "2/6" "시스템 패키지 설치 (관리자 비밀번호를 물어볼 수 있습니다)"

# 패키지를 **한 덩어리로 설치하지 않는다**: 하나라도 그 배포판에 없으면
# apt 가 통째로 실패해 설치 전체가 멈춘다(실측: 파이 전용 python3-picamera2 가
# 없는 리눅스에서 전 과정 중단). 역할별로 나눠 개별 설치하고, 없어도 되는
# 것은 경고만 남긴다.
#   필수  : 이게 없으면 프로그램이 아예 못 돈다 → 실패 처리
#   카메라: 실제 촬영에만 필요(없으면 나중에 설치해도 됨) → 경고
#   빌드  : 화면 만들기에만 필요 → 경고(검사 자체는 동작)
PKGS_REQUIRED="git python3-venv python3-pip"
PKGS_CAMERA="python3-picamera2 python3-opencv"
PKGS_BUILD="nodejs npm"

apt_install_one() {   # $1=패키지  → 0 성공/이미설치, 1 실패
  dpkg -s "$1" >/dev/null 2>&1 && return 0
  $SUDO apt-get install -y -qq "$1" >/dev/null 2>&1
}

if command -v apt-get >/dev/null 2>&1; then
  info "(네트워크 속도에 따라 수 분 걸립니다)"
  $SUDO apt-get update -qq >/dev/null 2>&1 || warn "패키지 목록 갱신 실패 — 계속 진행합니다."

  FAILED_REQ=""
  for p in $PKGS_REQUIRED; do
    apt_install_one "$p" || FAILED_REQ="$FAILED_REQ $p"
  done
  [ -n "$FAILED_REQ" ] && die "[2/6] 필수 패키지 설치 실패:$FAILED_REQ
    인터넷 연결을 확인한 뒤 다시 실행하세요."
  ok "필수 패키지 준비 완료"

  MISSING_CAM=""
  for p in $PKGS_CAMERA; do
    apt_install_one "$p" || MISSING_CAM="$MISSING_CAM $p"
  done
  if [ -n "$MISSING_CAM" ]; then
    warn "카메라 관련 패키지를 설치하지 못했습니다:$MISSING_CAM"
    warn "설치는 계속합니다. 실제 촬영을 하려면 나중에 다음을 실행하세요:"
    warn "  sudo apt install$MISSING_CAM   (그 뒤 이 스크립트를 다시 실행)"
  else
    ok "카메라 패키지 준비 완료"
  fi

  MISSING_BUILD=""
  for p in $PKGS_BUILD; do
    apt_install_one "$p" || MISSING_BUILD="$MISSING_BUILD $p"
  done
  if [ -n "$MISSING_BUILD" ]; then
    warn "화면 빌드용 패키지를 설치하지 못했습니다:$MISSING_BUILD (화면 만들기를 건너뜁니다)"
    DO_BUILD=false
  else
    ok "빌드 도구 준비 완료"
  fi
else
  warn "apt 가 없는 시스템입니다. 필요한 프로그램(git/python3/nodejs)을 직접 설치해야 합니다."
fi

# 노드 버전 확인(화면 빌드에 18 이상 필요).
if command -v node >/dev/null 2>&1; then
  NODE_MAJOR="$(node -v 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/')"
  info "Node.js : $(node -v 2>/dev/null)"
  if [ -n "$NODE_MAJOR" ] && [ "$NODE_MAJOR" -lt 18 ] 2>/dev/null; then
    warn "Node.js 18 이상이 필요합니다. 화면 빌드가 실패하면 Node 를 올려주세요."
  fi
elif $DO_BUILD; then
  warn "Node.js 가 없어 화면을 만들 수 없습니다. --no-build 로 건너뛰거나 Node.js 를 설치하세요."
fi

# =============================================================================
# [3/6] 파이썬 환경
# =============================================================================
step "3/6" "파이썬 환경 만들기"

# (a) 검사 워커 — 카메라 라이브러리(picamera2/cv2)를 시스템에서 상속해야 하므로
#     반드시 --system-site-packages 로 만든다. 이게 핵심이다.
WORKER_VENV="$REPO/services/vision/.venv"
if [ ! -x "$WORKER_VENV/bin/python" ]; then
  info "검사 워커용 환경 생성 중… (카메라 라이브러리 상속)"
  python3 -m venv --system-site-packages "$WORKER_VENV" \
    || die "[3/6] 워커 환경 생성 실패"
fi
info "검사 워커 패키지 설치 중… (수 분 걸릴 수 있습니다)"
"$WORKER_VENV/bin/pip" install -q --upgrade pip >/dev/null 2>&1
"$WORKER_VENV/bin/pip" install -q httpx numpy \
  || die "[3/6] 워커 패키지 설치 실패 — 인터넷 연결을 확인하세요."
"$WORKER_VENV/bin/pip" install -q -e "$REPO/packages/shared-types/python" \
  || die "[3/6] 공용 스키마 설치 실패"

# --- 상속받은 numpy 가 **실제로 동작하는지** 확인하고, 아니면 고친다 ---
# --system-site-packages 환경은 시스템 numpy 를 물려받는데, 그게 깨져 있으면
# pip 는 "이미 설치됨"으로 판단해 건너뛰고, 검사는 **실행 시점에** 죽는다
# (실측: 시스템 numpy 의 C 확장 로드 실패). 설치 때 걸러 자동 복구한다.
# 주의: 정상일 때는 시스템 numpy 를 그대로 둔다 — 시스템 opencv/picamera2 가
# 그 numpy 에 맞춰 빌드돼 있어, 다른 버전으로 덮으면 오히려 깨진다.
if ! "$WORKER_VENV/bin/python" -c "import numpy" >/dev/null 2>&1; then
  warn "시스템 numpy 가 정상 동작하지 않아 이 환경 전용으로 다시 설치합니다."
  "$WORKER_VENV/bin/pip" install -q --ignore-installed "numpy>=1.26,<2.2" \
    || die "[3/6] numpy 재설치 실패"
  "$WORKER_VENV/bin/python" -c "import numpy" >/dev/null 2>&1 \
    || die "[3/6] numpy 를 사용할 수 없습니다. 다음을 실행한 뒤 다시 시도하세요:
    rm -rf '$WORKER_VENV'"
  ok "numpy 복구 완료"
fi

# --- 영상 라이브러리(cv2)는 **필수** --------------------------------------
# 검사 파이프라인이 무조건 import 하므로, 없으면 카메라 없이 하는 테스트조차
# 워커가 뜨지 않는다(실측: ModuleNotFoundError: cv2 로 기동 실패). apt 로 못
# 받았으면 이 환경에 pip 로 채워 넣는다(requirements 에 있는 headless 판).
if ! "$WORKER_VENV/bin/python" -c "import cv2" >/dev/null 2>&1; then
  warn "영상 라이브러리(opencv)가 없어 설치합니다… (수 분 걸릴 수 있습니다)"
  "$WORKER_VENV/bin/pip" install -q "opencv-python-headless>=4.9,<5" \
    || die "[3/6] 영상 라이브러리 설치 실패. 다음을 먼저 실행해 보세요:
    sudo apt install python3-opencv"
  "$WORKER_VENV/bin/python" -c "import cv2" >/dev/null 2>&1 \
    || die "[3/6] 영상 라이브러리를 사용할 수 없습니다(설치는 되었으나 로드 실패)."
  ok "영상 라이브러리 설치 완료"
fi

# --- 카메라 라이브러리(picamera2)는 실제 촬영에만 필요 ---------------------
# 없어도 시뮬레이터(AIVIS_CAMERA=sim)로 전 과정을 시험할 수 있다.
if "$WORKER_VENV/bin/python" -c "import picamera2" >/dev/null 2>&1; then
  ok "검사 워커 환경 준비 완료 (카메라 연결됨)"
else
  ok "검사 워커 환경 준비 완료 (카메라 없이 시험 가능)"
  warn "카메라 라이브러리(picamera2)가 없어 **실제 촬영은 되지 않습니다**."
  warn "촬영하려면:  sudo apt install python3-picamera2   → 이 스크립트를 다시 실행"
fi

# (b) API — 독립된 환경(시스템 패키지 상속 불필요).
API_VENV="$REPO/services/api/.venv-api"
if [ ! -x "$API_VENV/bin/python" ]; then
  info "API 환경 생성 중…"
  python3 -m venv "$API_VENV" || die "[3/6] API 환경 생성 실패"
fi
info "API 패키지 설치 중… (파이에서 수 분 걸립니다)"
"$API_VENV/bin/pip" install -q --upgrade pip >/dev/null 2>&1
"$API_VENV/bin/pip" install -q -r "$REPO/services/api/requirements.txt" \
  || die "[3/6] API 패키지 설치 실패 — 인터넷 연결을 확인하세요."
"$API_VENV/bin/pip" install -q -e "$REPO/packages/shared-types/python" \
  || die "[3/6] 공용 스키마 설치 실패(API)"
ok "API 환경 준비 완료"

# =============================================================================
# [4/6] 화면 만들기
# =============================================================================
step "4/6" "화면 만들기 (작업자 화면 + 관리자 대시보드)"

if ! $DO_BUILD; then
  info "--no-build 지정 → 건너뜁니다."
elif ! command -v npm >/dev/null 2>&1; then
  warn "npm 이 없어 건너뜁니다. 화면 없이 검사만 동작합니다."
else
  cd "$REPO" || die "저장소로 이동 실패"

  # 화면 빌드는 파이에서 가장 잘 깨지는 단계다(디스크·메모리를 크게 쓴다).
  # 실패한 뒤 "인터넷/디스크를 확인하세요" 같은 **추측**만 내놓으면 사용자는
  # 아무것도 할 수 없다. 그래서 (1) 시작 전에 자원을 재보고, (2) 실패하면
  # npm 이 남긴 실제 로그를 화면에 꺼내 보여준다.
  BUILD_MIN_MB=1500   # node_modules 만 1GB 이상. 여유까지 감안한 하한.
  FREE_MB="$(df -Pm "$REPO" 2>/dev/null | awk 'NR==2{print $4}')"
  if [ -n "${FREE_MB:-}" ] && [ "$FREE_MB" -lt "$BUILD_MIN_MB" ]; then
    die "[4/6] 디스크 여유가 부족합니다 (남은 공간 ${FREE_MB}MB, 최소 ${BUILD_MIN_MB}MB 필요).
    화면 만들기에는 1GB 이상이 필요합니다. 공간을 확보한 뒤 다시 실행하세요:
      sudo apt clean            # 패키지 캐시 삭제
      rm -rf ~/.npm/_cacache    # npm 캐시 삭제
      df -h $REPO               # 남은 공간 확인
    공간 확보가 어려우면 화면 없이 먼저 설치할 수 있습니다(검사는 동작):
      bash scripts/aivis-install.sh --no-build"
  fi

  # 메모리 부족(OOM)은 파이 4에서 흔하다 — 미리 알려 스왑을 늘리게 한다.
  MEM_TOTAL_MB="$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo 2>/dev/null)"
  SWAP_MB="$(awk '/SwapTotal/{print int($2/1024)}' /proc/meminfo 2>/dev/null)"
  if [ -n "${MEM_TOTAL_MB:-}" ] && [ "$MEM_TOTAL_MB" -lt 4500 ] \
     && [ -n "${SWAP_MB:-}" ] && [ "$SWAP_MB" -lt 1000 ]; then
    warn "메모리 ${MEM_TOTAL_MB}MB / 스왑 ${SWAP_MB}MB — 빌드 중 메모리 부족이 날 수 있습니다."
    warn "실패하면 스왑을 2GB 로 늘린 뒤 다시 시도하세요(아래 실패 안내에 명령 있음)."
  fi

  # npm 실패 시 npm 이 남긴 디버그 로그의 **핵심 줄**을 꺼내 보여준다.
  show_npm_log() {
    local logdir="${HOME}/.npm/_logs"
    local latest
    latest="$(ls -t "$logdir"/*debug*.log 2>/dev/null | head -1)"
    [ -n "$latest" ] || return 0
    echo >&2
    echo >&2 "  ── npm 오류 로그 (마지막 부분) ──────────────────────────"
    grep -iE "error|ENOSPC|ENOMEM|ETIMEDOUT|ENOTFOUND|EACCES|killed" "$latest" \
      | tail -12 | sed 's/^/  /' >&2 || tail -12 "$latest" | sed 's/^/  /' >&2
    echo >&2 "  ────────────────────────────────────────────────────────"
    echo >&2 "  전체 로그: $latest"
  }

  if [ ! -d node_modules ]; then
    info "화면 재료 내려받는 중… (파이에서 10분 이상 걸릴 수 있습니다)"
    if ! npm install --no-audit --no-fund; then
      show_npm_log
      die "[4/6] 화면 재료 내려받기(npm install) 실패.
    위 로그에서 원인을 확인하세요. 흔한 원인과 조치:
      · ENOSPC(공간 부족)  → sudo apt clean; rm -rf ~/.npm/_cacache
      · Killed / ENOMEM(메모리 부족) → 스왑 2GB 로 늘린 뒤 재시도:
          sudo dphys-swapfile swapoff
          sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile
          sudo dphys-swapfile setup && sudo dphys-swapfile swapon
      · ETIMEDOUT / ENOTFOUND(네트워크) → 인터넷 연결 확인 후 재시도
    급하면 화면 없이 먼저 설치할 수 있습니다(검사는 동작):
      bash scripts/aivis-install.sh --no-build"
    fi
  fi
  info "작업자 화면 만드는 중…"
  npm run build --workspace @aivis/hmi >/dev/null || {
    show_npm_log
    die "[4/6] 작업자 화면 빌드 실패 (메모리 부족이면 위 스왑 안내를 참고하세요)."
  }
  info "관리자 대시보드 만드는 중…"
  npm run build --workspace @aivis/dashboard >/dev/null || {
    show_npm_log
    die "[4/6] 관리자 대시보드 빌드 실패 (메모리 부족이면 위 스왑 안내를 참고하세요)."
  }
  ok "화면 준비 완료"
fi

# =============================================================================
# [5/6] 부팅 자동실행 등록
# =============================================================================
step "5/6" "부팅 자동실행 등록"

SERVICE_OK=false
if ! $DO_SERVICE; then
  info "--no-service 지정 → 등록하지 않습니다. 수동 실행:  bash scripts/aivis.sh start"
elif ! command -v systemctl >/dev/null 2>&1; then
  warn "이 시스템에는 systemd 가 없어 자동실행을 등록할 수 없습니다."
  info "수동 실행:  bash scripts/aivis.sh start"
else
  info "전원을 켜면 검사가 저절로 시작되도록 등록합니다."
  if $SUDO bash "$SCRIPT_DIR/aivis-install-service.sh" --user "$RUN_USER" --no-start; then
    SERVICE_OK=true
    ok "부팅 자동실행 등록 완료"
  else
    warn "자동실행 등록에 실패했습니다. 수동 실행은 가능합니다:  bash scripts/aivis.sh start"
  fi
fi

# =============================================================================
# [6/6] 시작
# =============================================================================
step "6/6" "시작"

if $SERVICE_OK; then
  info "서비스 시작 중…"
  $SUDO systemctl restart aivis-standalone.service \
    || die "[6/6] 서비스 시작 실패 — 로그:  journalctl -u aivis-standalone -n 50"
  # 기동 대기(파이에서는 수십 초 걸릴 수 있다).
  info "기동 확인 중… (최대 3분)"
  READY=false
  for _ in $(seq 1 90); do
    if curl -sf "http://127.0.0.1:8000/health" >/dev/null 2>&1; then READY=true; break; fi
    sleep 2
  done
  if $READY; then
    ok "검사 시스템이 시작되었습니다"
  else
    warn "아직 응답이 없습니다. 잠시 후 다시 확인하세요:  bash scripts/aivis.sh status"
    warn "문제가 계속되면 로그:  journalctl -u aivis-standalone -n 50"
  fi
else
  info "지금 시작하려면:  bash scripts/aivis.sh start"
fi

# --- 마무리 안내 ---
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -z "${IP:-}" ] && IP="<파이IP>"
echo
echo "==============================================================="
echo "  ${C_G}${C_B}설치가 끝났습니다${C_0}"
echo "---------------------------------------------------------------"
echo "   작업자 화면 (HMI)   http://$IP:5173"
echo "   관리자 대시보드      http://$IP:5174"
echo "   파이 화면에서는      http://localhost:5173"
echo
echo "   로그인   아이디: admin   비밀번호: aivis1234"
echo "   ${C_Y}첫 로그인 후 비밀번호를 바꾸세요.${C_0}"
echo "---------------------------------------------------------------"
if $SERVICE_OK; then
  echo "   ${C_G}전원을 켜면 자동으로 시작됩니다.${C_0}"
  echo "   이후 업데이트는 관리자 대시보드 → '프로그램 업데이트' 에서 버튼으로."
else
  echo "   시작/중지/상태 확인:  bash scripts/aivis.sh"
fi
echo "==============================================================="
