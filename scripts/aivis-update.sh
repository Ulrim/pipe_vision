#!/usr/bin/env bash
# =============================================================================
# AIVIS 원클릭 업데이트 — 라즈베리파이 현장에서 최신 코드로 안전하게 갱신
# ---------------------------------------------------------------------------
# 현장 담당자는 이것 하나만 실행하면 된다:
#     bash scripts/aivis-update.sh
#
# 하는 일(5단계):
#   [1/5] 사전 점검 — 로컬 변경이 있으면 자동 보관(git stash -u)
#   [2/5] 최신 코드 받기 — git fetch + 대상 브랜치로 전환/갱신
#   [3/5] 변경 분석 — 무엇이 바뀌었는지 보고 필요한 재빌드만 고른다
#   [4/5] 재빌드 — 바뀐 부분만(파이에서 10분 낭비 방지)
#   [5/5] 재시작 — systemd 로 운영 중이면 서비스 재시작
#
# 옵션:
#   --dry-run          실제로 바꾸지 않고 "무엇을 할지"만 보여준다(네트워크 미사용)
#   --restart          업데이트 후 서비스 자동 재시작(묻지 않음)
#   --no-restart       재시작하지 않음(안내만)
#   --rollback         직전 업데이트 이전 커밋으로 되돌린다
#   --branch <이름>    대상 브랜치 지정(기본: 아래 AIVIS_BRANCH)
#   --skip-build       코드만 받고 재빌드는 건너뜀
#   -h, --help         도움말
#
# 환경변수:
#   AIVIS_BRANCH=claude/eloquent-gauss-O6wDP   대상 브랜치
#   AIVIS_HOME=/var/lib/aivis                  데이터 루트(롤백 기록 저장 위치)
#
# 안전 원칙:
#   · 검사 데이터(DB/이미지/스풀)는 AIVIS_HOME 에 있고 git 관리 밖이라 절대
#     사라지지 않는다. 이 스크립트는 프로그램 코드만 교체한다.
#   · 로컬 수정본이 있으면 지우지 않고 stash(보관)한 뒤 복구 명령을 알려준다.
#   · 실패 시 자동 롤백은 하지 않는다(원인 파악 우선). 되돌리는 명령을 안내한다.
# =============================================================================
set -uo pipefail

# --- 저장소 루트(스크립트 위치 기준) ---------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

AIVIS_BRANCH="${AIVIS_BRANCH:-claude/eloquent-gauss-O6wDP}"
AIVIS_HOME="${AIVIS_HOME:-/var/lib/aivis}"
SERVICE_NAME="${AIVIS_SERVICE_NAME:-aivis-standalone.service}"

DRY_RUN=false
DO_ROLLBACK=false
SKIP_BUILD=false
RESTART_MODE="ask"   # ask | yes | no

# --- 출력 헬퍼 --------------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_B=$'\033[36m'; C_0=$'\033[0m'
else
  C_R=""; C_G=""; C_Y=""; C_B=""; C_0=""
fi
step()  { echo; echo "${C_B}[$1] $2${C_0}"; }
info()  { echo "    $*"; }
ok()    { echo "    ${C_G}[성공]${C_0} $*"; }
warn()  { echo "    ${C_Y}[주의]${C_0} $*"; }
die() {
  echo >&2
  echo >&2 "${C_R}[실패] $*${C_0}"
  echo >&2
  echo >&2 "  복구 방법:"
  echo >&2 "   · 이 화면을 그대로 캡처해서 개발사에 전달하면 원인 파악이 빠릅니다."
  if [ -n "${PREV_COMMIT:-}" ]; then
    echo >&2 "   · 업데이트 전 상태로 되돌리기:  bash scripts/aivis-update.sh --rollback"
    echo >&2 "     (또는 수동: git -C '$REPO' checkout $PREV_COMMIT)"
  fi
  if [ -n "${STASH_NAME:-}" ]; then
    echo >&2 "   · 보관해 둔 로컬 변경 복구:  git -C '$REPO' stash pop"
  fi
  exit 1
}

# 파일 상단 주석 블록(두 번째 '# =====' 줄까지)을 그대로 도움말로 출력한다.
usage() { sed -n '3,/^# =\{10,\}/p' "${BASH_SOURCE[0]}" | sed 's/^#\( \|$\)//;s/^# \{0,1\}//'; }

# --- 인자 파싱 --------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)    DRY_RUN=true ;;
    --rollback)   DO_ROLLBACK=true ;;
    --restart)    RESTART_MODE=yes ;;
    --no-restart) RESTART_MODE=no ;;
    --skip-build) SKIP_BUILD=true ;;
    --branch)     shift; [ $# -gt 0 ] || die "--branch 뒤에 브랜치 이름이 필요합니다."; AIVIS_BRANCH="$1" ;;
    -h|--help)    usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1"; echo; usage; exit 2 ;;
  esac
  shift
done

command -v git >/dev/null 2>&1 || die "git 이 설치되어 있지 않습니다.  sudo apt install -y git"
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || die "여기는 git 저장소가 아닙니다: $REPO"

# --- 롤백 기록 파일 위치 ----------------------------------------------------
# AIVIS_HOME 이 쓰기 가능하면 거기에(재설치해도 남음), 아니면 .git 내부에 둔다.
if { [ -d "$AIVIS_HOME" ] || { ! $DRY_RUN && mkdir -p "$AIVIS_HOME" 2>/dev/null; }; } \
   && [ -w "$AIVIS_HOME" ]; then
  STATE_FILE="$AIVIS_HOME/.last_good_commit"
else
  # 데이터 루트가 없거나 권한이 없으면 저장소의 .git 안에 기록(작업 트리 오염 없음)
  STATE_FILE="$(git -C "$REPO" rev-parse --git-dir)/aivis-last-good-commit"
fi

run() { # 실제 실행(드라이런이면 표시만)
  if $DRY_RUN; then
    info "${C_Y}(모의)${C_0} $*"
    return 0
  fi
  "$@"
}

echo "==============================================================="
echo "  AIVIS 업데이트  $( $DRY_RUN && echo '(모의 실행 — 아무것도 바꾸지 않습니다)' )"
echo "  저장소 : $REPO"
echo "  브랜치 : $AIVIS_BRANCH"
echo "  데이터 : $AIVIS_HOME  (검사 DB·이미지 — 업데이트와 무관하게 보존)"
echo "==============================================================="

# =============================================================================
# 롤백 모드
# =============================================================================
if $DO_ROLLBACK; then
  step "롤백" "직전 업데이트 이전 커밋으로 되돌립니다"
  [ -f "$STATE_FILE" ] || die "되돌릴 기록이 없습니다($STATE_FILE). 이 파이에서 아직 업데이트를 한 적이 없습니다."
  TARGET="$(cat "$STATE_FILE")"
  [ -n "$TARGET" ] || die "롤백 기록 파일이 비어 있습니다: $STATE_FILE"
  info "되돌릴 커밋: $TARGET"
  info "$(git -C "$REPO" log -1 --format='  %h %s (%ci)' "$TARGET" 2>/dev/null || echo '  (커밋 정보를 읽을 수 없음)')"
  run git -C "$REPO" checkout "$TARGET" || die "롤백 실패 — 로컬 변경이 남아 있을 수 있습니다. git -C '$REPO' status 확인"
  ok "코드를 이전 상태로 되돌렸습니다."
  info "이 상태는 '분리된 HEAD' 입니다. 다시 최신으로 가려면: bash scripts/aivis-update.sh"
  info "프런트엔드/의존이 이전 버전과 다르면 재빌드가 필요할 수 있습니다."
  info "서비스 재시작:  bash scripts/aivis.sh restart"
  exit 0
fi

# =============================================================================
# [1/5] 사전 점검
# =============================================================================
step "1/5" "사전 점검 — 현재 상태 확인"
PREV_COMMIT="$(git -C "$REPO" rev-parse HEAD)"
PREV_BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '(분리된 HEAD)')"
info "현재 브랜치 : $PREV_BRANCH"
info "현재 커밋   : $(git -C "$REPO" log -1 --format='%h %s' 2>/dev/null)"

STASH_NAME=""
DIRTY="$(git -C "$REPO" status --porcelain 2>/dev/null)"
if [ -n "$DIRTY" ]; then
  CHANGED_N="$(printf '%s\n' "$DIRTY" | wc -l | tr -d ' ')"
  warn "이 파이에서 수정되었거나 새로 생긴 파일이 ${CHANGED_N}개 있습니다."
  printf '%s\n' "$DIRTY" | head -10 | sed 's/^/      /'
  [ "$CHANGED_N" -gt 10 ] && info "      … 외 $((CHANGED_N - 10))개"
  STASH_NAME="aivis-update-$(date +%Y%m%d-%H%M%S)"
  info "삭제하지 않고 안전하게 보관합니다(stash): $STASH_NAME"
  if $DRY_RUN; then
    info "${C_Y}(모의)${C_0} git stash push -u -m $STASH_NAME"
  else
    git -C "$REPO" stash push -u -m "$STASH_NAME" >/dev/null \
      || die "로컬 변경 보관(stash) 실패 — git -C '$REPO' status 로 확인하세요."
    ok "보관 완료. 되살리려면:  git -C '$REPO' stash pop"
    info "보관 목록 확인:  git -C '$REPO' stash list"
  fi
else
  ok "로컬 변경 없음 — 깨끗한 상태입니다."
fi
info "검사 데이터(DB/이미지/스풀)는 $AIVIS_HOME 에 있어 이 작업의 영향을 받지 않습니다."

# =============================================================================
# [2/5] 최신 코드 받기
# =============================================================================
step "2/5" "최신 코드 받는 중… (네트워크 속도에 따라 수십 초)"
if $DRY_RUN; then
  info "${C_Y}(모의)${C_0} git fetch origin (네트워크 사용 안 함 — 이미 받아둔 origin/$AIVIS_BRANCH 로 비교)"
else
  git -C "$REPO" fetch origin --prune \
    || die "[2/5] 최신 코드 받기 실패 — 인터넷/사내망 연결과 GitHub 접속을 확인하세요. (ping github.com)"
  ok "원격 정보 갱신 완료"
fi

if git -C "$REPO" rev-parse --verify -q "origin/$AIVIS_BRANCH" >/dev/null; then
  REMOTE_REF="origin/$AIVIS_BRANCH"
else
  if $DRY_RUN; then
    warn "origin/$AIVIS_BRANCH 를 로컬에서 찾지 못했습니다(모의 실행이라 fetch 를 안 했기 때문일 수 있음)."
    REMOTE_REF="HEAD"
  else
    die "[2/5] 원격 브랜치 origin/$AIVIS_BRANCH 가 없습니다. --branch 로 올바른 브랜치를 지정하거나 AIVIS_BRANCH 를 확인하세요."
  fi
fi

TARGET_COMMIT="$(git -C "$REPO" rev-parse "$REMOTE_REF")"
if [ "$TARGET_COMMIT" = "$PREV_COMMIT" ]; then
  ok "이미 최신입니다(받을 변경 없음)."
else
  AHEAD="$(git -C "$REPO" rev-list --count "$PREV_COMMIT..$TARGET_COMMIT" 2>/dev/null || echo '?')"
  info "새 커밋 ${AHEAD}개를 적용합니다."
fi

if $DRY_RUN; then
  info "${C_Y}(모의)${C_0} git checkout -B $AIVIS_BRANCH $REMOTE_REF"
else
  if [ "$PREV_BRANCH" = "$AIVIS_BRANCH" ]; then
    git -C "$REPO" merge --ff-only "$REMOTE_REF" \
      || die "[2/5] 브랜치 갱신 실패(빨리감기 불가). 되돌리기:  git -C '$REPO' checkout -B $AIVIS_BRANCH $REMOTE_REF --force"
  else
    git -C "$REPO" checkout -B "$AIVIS_BRANCH" "$REMOTE_REF" \
      || die "[2/5] 브랜치 전환 실패 — 위 git 메시지를 확인하세요."
  fi
  echo "$PREV_COMMIT" > "$STATE_FILE" 2>/dev/null || warn "롤백 기록 저장 실패: $STATE_FILE"
  ok "코드 갱신 완료 → $(git -C "$REPO" log -1 --format='%h %s')"
  info "문제가 생기면 되돌리기:  bash scripts/aivis-update.sh --rollback"
fi

NEW_COMMIT="$(git -C "$REPO" rev-parse HEAD)"

# =============================================================================
# [3/5] 변경 분석 — 필요한 재빌드만 고른다
# =============================================================================
step "3/5" "변경 내용 분석 — 다시 만들어야 할 부분만 고릅니다"
if $DRY_RUN; then
  DIFF_BASE="$PREV_COMMIT"; DIFF_HEAD="$TARGET_COMMIT"
else
  DIFF_BASE="$PREV_COMMIT"; DIFF_HEAD="$NEW_COMMIT"
fi

CHANGED_FILES=""
if [ "$DIFF_BASE" != "$DIFF_HEAD" ]; then
  CHANGED_FILES="$(git -C "$REPO" diff --name-only "$DIFF_BASE" "$DIFF_HEAD" 2>/dev/null)"
fi

need() { printf '%s\n' "$CHANGED_FILES" | grep -Eq "$1"; }

NEED_NPM_INSTALL=false
NEED_HMI=false
NEED_DASHBOARD=false
NEED_API_DEPS=false
NEED_WORKER_DEPS=false

if [ -z "$CHANGED_FILES" ]; then
  ok "바뀐 파일 없음 — 재빌드가 필요 없습니다."
else
  N_CHANGED="$(printf '%s\n' "$CHANGED_FILES" | wc -l | tr -d ' ')"
  info "바뀐 파일 ${N_CHANGED}개"
  need '^(package\.json|package-lock\.json)$'                  && NEED_NPM_INSTALL=true
  need '^(apps/hmi/|packages/)'                                && NEED_HMI=true
  need '^(apps/dashboard/|packages/)'                          && NEED_DASHBOARD=true
  need '^(services/api/requirements\.txt|packages/shared-types/)' && NEED_API_DEPS=true
  need '^services/vision/requirements\.txt$'                   && NEED_WORKER_DEPS=true
  $NEED_NPM_INSTALL   && info "  · npm 패키지 목록 변경 → npm install 필요"
  $NEED_API_DEPS      && info "  · API 의존/공용타입 변경 → API 파이썬 패키지 재설치 필요"
  $NEED_WORKER_DEPS   && info "  · 워커 의존 변경 → 워커 파이썬 패키지 재설치 필요"
  $NEED_HMI           && info "  · 작업자 화면(HMI) 변경 → 화면 다시 만들기 필요"
  $NEED_DASHBOARD     && info "  · 관리자 대시보드 변경 → 화면 다시 만들기 필요"
  if ! $NEED_NPM_INSTALL && ! $NEED_HMI && ! $NEED_DASHBOARD && ! $NEED_API_DEPS && ! $NEED_WORKER_DEPS; then
    ok "코드만 바뀌었습니다 — 재빌드 없이 재시작만 하면 됩니다."
  fi
fi

# =============================================================================
# [4/5] 재빌드 (바뀐 것만)
# =============================================================================
step "4/5" "필요한 부분만 다시 만드는 중…"
if $SKIP_BUILD; then
  warn "--skip-build 지정 → 재빌드를 모두 건너뜁니다."
else
  # --- 파이썬: API ---
  API_VENV="${AIVIS_API_VENV:-$REPO/services/api/.venv-api/bin/python}"
  if $NEED_API_DEPS; then
    if [ -x "$API_VENV" ]; then
      info "API 파이썬 패키지 설치 중… (수 분 걸릴 수 있습니다)"
      run "$API_VENV" -m pip install -r "$REPO/services/api/requirements.txt" \
        || die "[4/5] API 파이썬 패키지 설치 실패 — 인터넷 연결 또는 pip 로그를 확인하세요."
      run "$API_VENV" -m pip install -e "$REPO/packages/shared-types/python" \
        || die "[4/5] 공용 타입(aivis_types) 설치 실패."
      ok "API 패키지 갱신 완료"
    else
      warn "API venv 가 없습니다($API_VENV) → 다음 기동 시 aivis-standalone.sh 가 자동 생성/설치합니다."
    fi
  else
    info "API 파이썬 패키지: 변경 없음 (건너뜀)"
  fi

  # --- 파이썬: 워커 ---
  # 주의: 워커 venv 는 --system-site-packages 로 만들어 picamera2/cv2 를 상속한다.
  #       재생성하면 카메라가 안 잡히므로 절대 지우지 않고 pip install 만 한다.
  WORKER_VENV="${AIVIS_WORKER_VENV:-$REPO/services/vision/.venv/bin/python}"
  if $NEED_WORKER_DEPS; then
    if [ -x "$WORKER_VENV" ]; then
      info "워커 파이썬 패키지 설치 중… (venv 는 재생성하지 않습니다 — 카메라 연동 보호)"
      run "$WORKER_VENV" -m pip install -r "$REPO/services/vision/requirements.txt" \
        || die "[4/5] 워커 파이썬 패키지 설치 실패 — 위 pip 로그를 확인하세요."
      ok "워커 패키지 갱신 완료"
    else
      warn "워커 venv 가 없습니다($WORKER_VENV) → docs/RASPBERRY_PI.md STEP 3 의 --system-site-packages venv 를 먼저 만드세요."
    fi
  else
    info "워커 파이썬 패키지: 변경 없음 (건너뜀)"
  fi

  # --- 프런트엔드 ---
  if $NEED_HMI || $NEED_DASHBOARD || $NEED_NPM_INSTALL; then
    if ! command -v npm >/dev/null 2>&1; then
      warn "npm(Node.js)이 설치되어 있지 않아 화면 빌드를 건너뜁니다."
      info "  설치:  sudo apt install -y nodejs npm    (또는 사무실 PC 에서 빌드 후 dist 폴더 복사)"
      info "  화면 빌드를 건너뛰어도 검사 기능(API·워커)은 정상 동작합니다."
    else
      if $NEED_NPM_INSTALL || [ ! -d "$REPO/node_modules" ]; then
        info "npm 패키지 설치 중… (파이에서 수 분 소요)"
        run npm --prefix "$REPO" install \
          || die "[4/5] npm install 실패 — 인터넷 연결(레지스트리 접근)을 확인하세요."
      fi
      if $NEED_HMI; then
        info "작업자 화면(HMI) 빌드 중…"
        run npm --prefix "$REPO" run build --workspace @aivis/hmi \
          || die "[4/5] HMI 빌드 실패 — 이전 버전으로 되돌리려면: bash scripts/aivis-update.sh --rollback"
        ok "HMI 빌드 완료"
      fi
      if $NEED_DASHBOARD; then
        info "관리자 대시보드 빌드 중…"
        run npm --prefix "$REPO" run build --workspace @aivis/dashboard \
          || die "[4/5] 대시보드 빌드 실패 — 이전 버전으로 되돌리려면: bash scripts/aivis-update.sh --rollback"
        ok "대시보드 빌드 완료"
      fi
    fi
  else
    info "화면(HMI/대시보드): 변경 없음 (건너뜀)"
  fi
fi

# =============================================================================
# [5/5] 재시작
# =============================================================================
step "5/5" "서비스 재시작"
HAS_SYSTEMD=false
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files "$SERVICE_NAME" >/dev/null 2>&1 \
   && [ -f "/etc/systemd/system/$SERVICE_NAME" ]; then
  HAS_SYSTEMD=true
fi

if ! $HAS_SYSTEMD; then
  info "부팅 자동시작(systemd) 서비스가 설치되어 있지 않습니다."
  info "지금 실행 중인 창(Ctrl+C 로 종료 후)에서 다시 시작하세요:  bash scripts/aivis.sh start"
  info "부팅 시 자동시작 등록:  sudo bash scripts/aivis-install-service.sh"
else
  DO_RESTART=false
  case "$RESTART_MODE" in
    yes) DO_RESTART=true ;;
    no)  info "--no-restart 지정 → 재시작하지 않습니다. 나중에:  sudo systemctl restart $SERVICE_NAME" ;;
    ask)
      if [ -t 0 ]; then
        printf "    지금 서비스를 재시작할까요? 검사가 잠시 멈춥니다. [y/N] "
        read -r ans || ans=""
        case "$ans" in y|Y|yes|YES|ㅇ) DO_RESTART=true ;; *) info "재시작하지 않았습니다. 나중에:  sudo systemctl restart $SERVICE_NAME" ;; esac
      else
        info "자동 실행(비대화형) 환경이라 재시작을 생략합니다."
        info "재시작 명령:  sudo systemctl restart $SERVICE_NAME   (또는 --restart 옵션 사용)"
      fi
      ;;
  esac
  if $DO_RESTART; then
    if $DRY_RUN; then
      info "${C_Y}(모의)${C_0} systemctl restart $SERVICE_NAME"
    else
      if systemctl restart "$SERVICE_NAME" 2>/dev/null || sudo systemctl restart "$SERVICE_NAME"; then
        ok "서비스 재시작 완료 — 상태 확인:  systemctl status $SERVICE_NAME"
      else
        die "[5/5] 서비스 재시작 실패 — 로그 확인:  journalctl -u $SERVICE_NAME -n 50"
      fi
    fi
  fi
fi

echo
echo "==============================================================="
if $DRY_RUN; then
  echo "  ${C_G}모의 실행 완료${C_0} — 실제로는 아무것도 바뀌지 않았습니다."
  echo "  진짜로 업데이트하려면:  bash scripts/aivis-update.sh"
else
  echo "  ${C_G}업데이트 완료${C_0}  ($(git -C "$REPO" log -1 --format='%h %s'))"
  [ -n "$STASH_NAME" ] && echo "  보관된 로컬 변경: $STASH_NAME  (되살리기: git -C '$REPO' stash pop)"
  echo "  문제가 생기면 되돌리기:  bash scripts/aivis-update.sh --rollback"
fi
echo "  메뉴로 조작:  bash scripts/aivis.sh"
echo "==============================================================="
