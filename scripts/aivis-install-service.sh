#!/usr/bin/env bash
# =============================================================================
# AIVIS 부팅 자동시작 등록 — 파이를 켜면 검사 시스템이 저절로 뜨게 한다
# ---------------------------------------------------------------------------
# 사용(관리자 권한 필요):
#     sudo bash scripts/aivis-install-service.sh              # 등록 + 즉시 시작
#     sudo bash scripts/aivis-install-service.sh --uninstall  # 등록 해제
#     sudo bash scripts/aivis-install-service.sh --no-start   # 등록만(지금 시작 안 함)
#
# 하는 일:
#   deploy/aivis-standalone.service 를 현재 저장소 경로/실행 사용자에 맞게 고쳐
#   /etc/systemd/system/aivis-standalone.service 로 설치하고 enable --now 한다.
#
# ★ 주의: 엣지→클라우드 모드용 aivis-vision.service 와 동시에 켜지 마라.
#   (같은 카메라를 두 프로세스가 열어 충돌한다. 이 스크립트가 감지해 안내한다.)
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_UNIT="$REPO/deploy/aivis-standalone.service"
UNIT_NAME="aivis-standalone.service"
DEST_UNIT="/etc/systemd/system/$UNIT_NAME"

DO_UNINSTALL=false
DO_START=true
RUN_USER=""

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) DO_UNINSTALL=true ;;
    --no-start)  DO_START=false ;;
    --user)      shift; [ $# -gt 0 ] || { echo "--user 뒤에 사용자 이름이 필요합니다."; exit 2; }; RUN_USER="$1" ;;
    -h|--help)   sed -n '3,/^# =\{10,\}/p' "${BASH_SOURCE[0]}" | sed 's/^#\( \|$\)//;s/^# \{0,1\}//'; exit 0 ;;
    *) echo "알 수 없는 옵션: $1"; exit 2 ;;
  esac
  shift
done

say()  { echo "$*"; }
ok()   { echo "[성공] $*"; }
warn() { echo "[주의] $*"; }
die()  { echo "[실패] $*" >&2; exit 1; }

command -v systemctl >/dev/null 2>&1 \
  || die "이 시스템에는 systemd(systemctl)가 없습니다. 부팅 자동시작 대신 'bash scripts/aivis.sh start' 를 사용하세요."
[ "$(id -u)" = "0" ] \
  || die "관리자 권한이 필요합니다. 앞에 sudo 를 붙여 다시 실행하세요:  sudo bash scripts/aivis-install-service.sh"

# --- 제거 --------------------------------------------------------------------
if $DO_UNINSTALL; then
  say "부팅 자동시작 등록을 해제합니다…"
  systemctl disable --now "$UNIT_NAME" 2>/dev/null || warn "서비스가 이미 꺼져 있거나 등록되어 있지 않습니다."
  rm -f "$DEST_UNIT"
  systemctl daemon-reload
  ok "해제 완료. 이제 파이를 켜도 자동으로 시작하지 않습니다."
  say "직접 실행하려면:  bash scripts/aivis.sh start"
  exit 0
fi

[ -f "$SRC_UNIT" ] || die "유닛 원본이 없습니다: $SRC_UNIT"

# --- 실행 사용자 결정 ---------------------------------------------------------
# sudo 로 실행했다면 원래 로그인 사용자를, 아니면 pi(없으면 root) 를 쓴다.
if [ -z "$RUN_USER" ]; then
  RUN_USER="${SUDO_USER:-}"
  [ -n "$RUN_USER" ] && [ "$RUN_USER" != "root" ] || RUN_USER="pi"
  id "$RUN_USER" >/dev/null 2>&1 || RUN_USER="root"
fi
id "$RUN_USER" >/dev/null 2>&1 || die "사용자를 찾을 수 없습니다: $RUN_USER (--user 로 지정하세요)"
RUN_GROUP="$(id -gn "$RUN_USER")"

say "==============================================================="
say "  AIVIS 부팅 자동시작 등록"
say "  저장소   : $REPO"
say "  실행 사용자: $RUN_USER (그룹 $RUN_GROUP)"
say "  유닛     : $DEST_UNIT"
say "==============================================================="

# --- 카메라 권한 확인(경고만) -------------------------------------------------
if getent group video >/dev/null 2>&1; then
  if ! id -nG "$RUN_USER" 2>/dev/null | tr ' ' '\n' | grep -qx video; then
    warn "$RUN_USER 사용자가 video 그룹에 없습니다(카메라 접근 불가할 수 있음)."
    warn "  해결:  sudo usermod -aG video $RUN_USER   (그 뒤 재부팅)"
  fi
fi

# --- 충돌 유닛 확인 ------------------------------------------------------------
for other in aivis-vision.service aivis-vision-pi.service; do
  if systemctl is-enabled "$other" >/dev/null 2>&1; then
    warn "엣지→클라우드 모드 유닛 '$other' 가 켜져 있습니다."
    warn "  독립형과 동시에 쓰면 카메라 충돌·중복 검사가 발생합니다."
    warn "  끄려면:  sudo systemctl disable --now $other"
  fi
done

# --- 경로/사용자 치환 후 설치 ---------------------------------------------------
TMP_UNIT="$(mktemp)"
trap 'rm -f "$TMP_UNIT"' EXIT
sed \
  -e "s|^WorkingDirectory=.*|WorkingDirectory=$REPO|" \
  -e "s|^ExecStart=.*|ExecStart=/bin/bash $REPO/scripts/aivis-standalone.sh|" \
  -e "s|^User=.*|User=$RUN_USER|" \
  -e "s|^Group=.*|Group=$RUN_GROUP|" \
  -e "s|^Documentation=.*|Documentation=file://$REPO/docs/OPERATIONS_PI.md|" \
  "$SRC_UNIT" > "$TMP_UNIT" || die "유닛 파일 생성 실패"

install -m 0644 "$TMP_UNIT" "$DEST_UNIT" || die "유닛 설치 실패: $DEST_UNIT"
ok "유닛 설치 완료: $DEST_UNIT"

systemctl daemon-reload || die "systemctl daemon-reload 실패"

if $DO_START; then
  say "부팅 자동시작 등록 + 지금 시작…"
  if systemctl enable --now "$UNIT_NAME"; then
    ok "등록·기동 완료"
  else
    die "기동 실패 — 원인 로그:  journalctl -u $UNIT_NAME -n 50"
  fi
  sleep 3
  systemctl status "$UNIT_NAME" --no-pager -n 10 || true
else
  systemctl enable "$UNIT_NAME" || die "부팅 자동시작 등록 실패"
  ok "등록 완료(지금은 시작하지 않음). 시작:  sudo systemctl start $UNIT_NAME"
fi

say
say "==============================================================="
say "  이제 파이를 껐다 켜도 검사 시스템이 자동으로 실행됩니다."
say "  조작 메뉴 :  bash $REPO/scripts/aivis.sh"
say "  상태 확인 :  bash $REPO/scripts/aivis.sh status"
say "  로그 보기 :  journalctl -u $UNIT_NAME -f"
say "  등록 해제 :  sudo bash $REPO/scripts/aivis-install-service.sh --uninstall"
say "==============================================================="
