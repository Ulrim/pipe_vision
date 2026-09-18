#!/usr/bin/env bash
# =============================================================================
# AIVIS 통합 조작 메뉴 — 현장 담당자용 (리눅스 명령을 몰라도 번호만 누르면 됨)
# ---------------------------------------------------------------------------
# 사용:
#   bash scripts/aivis.sh              # 번호 선택 메뉴
#   bash scripts/aivis.sh <명령>       # 자동화/원격용 직접 실행
#
# 명령: start | stop | restart | status | monitor | update | logs | urls | help
#
# 환경변수(선택):
#   AIVIS_HOME=/var/lib/aivis   데이터 루트(로그·PID 저장)
#   API_PORT=8000  HMI_PORT=5173  DASHBOARD_PORT=5174
#   AIVIS_ADMIN_PASSWORD=aivis1234
#
# systemd 서비스(scripts/aivis-install-service.sh 로 등록)가 있으면 자동으로
# systemctl 을 쓰고, 없으면 scripts/aivis-standalone.sh 를 직접 띄운다.
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

AIVIS_HOME="${AIVIS_HOME:-/var/lib/aivis}"
API_PORT="${API_PORT:-8000}"
HMI_PORT="${HMI_PORT:-5173}"
DASHBOARD_PORT="${DASHBOARD_PORT:-5174}"
ADMIN_PW="${AIVIS_ADMIN_PASSWORD:-aivis1234}"
SERVICE_NAME="${AIVIS_SERVICE_NAME:-aivis-standalone.service}"

# 로그/PID 저장 위치(데이터 루트가 안 되면 /tmp 로 대체 — 권한 때문에 멈추지 않게)
STATE_DIR="$AIVIS_HOME"
if ! mkdir -p "$STATE_DIR/logs" 2>/dev/null || [ ! -w "$STATE_DIR" ]; then
  STATE_DIR="${TMPDIR:-/tmp}/aivis"
  mkdir -p "$STATE_DIR/logs" 2>/dev/null || true
fi
PIDFILE="$STATE_DIR/aivis-standalone.pid"
LOGFILE="$STATE_DIR/logs/standalone.log"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31;1m'; C_B=$'\033[36m'; C_D=$'\033[2m'; C_0=$'\033[0m'
else
  C_G=""; C_Y=""; C_R=""; C_B=""; C_D=""; C_0=""
fi
say()  { echo "$*"; }
ok()   { echo "${C_G}[성공]${C_0} $*"; }
warn() { echo "${C_Y}[주의]${C_0} $*"; }
err()  { echo "${C_R}[실패]${C_0} $*" >&2; }

PY="${AIVIS_PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY="python"

# --- systemd 사용 가능 여부 ---------------------------------------------------
has_systemd() {
  command -v systemctl >/dev/null 2>&1 && [ -f "/etc/systemd/system/$SERVICE_NAME" ]
}
sctl() { # 권한이 있으면 그대로, 없으면 sudo
  if systemctl "$@" 2>/dev/null; then return 0; fi
  sudo systemctl "$@"
}

# --- 직접 실행 모드 보조 -----------------------------------------------------
direct_pid() {
  [ -f "$PIDFILE" ] || return 1
  local pid; pid="$(cat "$PIDFILE" 2>/dev/null)"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  echo "$pid"
}

start_direct() {
  local pid
  if pid="$(direct_pid)"; then
    warn "이미 실행 중입니다 (PID $pid)."
    return 0
  fi
  say "AIVIS 를 백그라운드로 시작합니다… (로그: $LOGFILE)"
  if command -v setsid >/dev/null 2>&1; then
    setsid nohup bash "$SCRIPT_DIR/aivis-standalone.sh" >>"$LOGFILE" 2>&1 </dev/null &
  else
    nohup bash "$SCRIPT_DIR/aivis-standalone.sh" >>"$LOGFILE" 2>&1 </dev/null &
  fi
  local newpid=$!
  echo "$newpid" > "$PIDFILE"
  say "시작 신호를 보냈습니다 (PID $newpid). 준비될 때까지 최대 60초 기다립니다…"
  local i
  for i in $(seq 1 60); do
    if api_alive; then ok "시작 완료 — 검사 시스템이 응답합니다."; show_urls; return 0; fi
    if ! kill -0 "$newpid" 2>/dev/null; then
      rm -f "$PIDFILE"
      err "시작 도중 종료되었습니다. 원인 로그(마지막 20줄):"
      tail -n 20 "$LOGFILE" 2>/dev/null | sed 's/^/    /'
      err "전체 로그: $LOGFILE"
      return 1
    fi
    sleep 1
  done
  warn "60초 안에 API 응답이 없습니다. 로그를 확인하세요:  bash scripts/aivis.sh logs"
  return 1
}

stop_direct() {
  local pid
  if ! pid="$(direct_pid)"; then
    warn "실행 중이 아닙니다."
    rm -f "$PIDFILE"
    return 0
  fi
  say "중지 중… (PID $pid)"
  # setsid 로 띄웠으면 pid == 프로세스그룹 → 자식(api/워커/화면)까지 함께 종료
  kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
  local i
  for i in $(seq 1 20); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
  done
  if kill -0 "$pid" 2>/dev/null; then
    warn "정상 종료에 응답하지 않아 강제 종료합니다."
    kill -KILL -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
  fi
  rm -f "$PIDFILE"
  ok "중지 완료"
}

api_alive() {
  "$PY" - "$API_PORT" <<'PY' >/dev/null 2>&1
import sys, urllib.request
try:
    urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/health", timeout=2)
except Exception:
    sys.exit(1)
PY
}

# --- 명령 구현 ---------------------------------------------------------------
cmd_start() {
  if has_systemd; then
    say "서비스 시작: $SERVICE_NAME"
    sctl start "$SERVICE_NAME" && ok "시작 명령 완료" || { err "시작 실패 — journalctl -u $SERVICE_NAME -n 50"; return 1; }
    sleep 3; show_urls
  else
    start_direct
  fi
}

cmd_stop() {
  if has_systemd; then
    say "서비스 중지: $SERVICE_NAME"
    sctl stop "$SERVICE_NAME" && ok "중지 완료" || { err "중지 실패"; return 1; }
  else
    stop_direct
  fi
}

cmd_restart() {
  if has_systemd; then
    say "서비스 재시작: $SERVICE_NAME"
    sctl restart "$SERVICE_NAME" && ok "재시작 완료" || { err "재시작 실패 — journalctl -u $SERVICE_NAME -n 50"; return 1; }
    sleep 3; show_urls
  else
    stop_direct; start_direct
  fi
}

cmd_status() {
  echo "==============================================================="
  echo "  AIVIS 상태"
  echo "==============================================================="
  if has_systemd; then
    if sctl is-active --quiet "$SERVICE_NAME"; then
      ok "부팅 자동시작 서비스: 실행 중 ($SERVICE_NAME)"
    else
      warn "부팅 자동시작 서비스: 멈춤 ($SERVICE_NAME) → 시작하려면 메뉴 1번"
    fi
    systemctl status "$SERVICE_NAME" --no-pager -n 5 2>/dev/null | sed 's/^/    /'
  else
    local pid
    if pid="$(direct_pid)"; then
      ok "직접 실행 모드로 동작 중 (PID $pid)"
    else
      warn "실행 중이 아닙니다 (직접 실행 모드). 시작하려면 메뉴 1번"
    fi
    say "${C_D}부팅 자동시작을 원하면:  sudo bash scripts/aivis-install-service.sh${C_0}"
  fi
  echo
  if api_alive; then ok "API 응답 정상 (http://127.0.0.1:$API_PORT)"; else err "API 응답 없음 (http://127.0.0.1:$API_PORT)"; fi
  echo
  "$PY" "$SCRIPT_DIR/aivis-monitor.py" --once --url "http://127.0.0.1:$API_PORT" || true
}

cmd_monitor() {
  say "실시간 모니터를 시작합니다. 종료하려면 Ctrl+C."
  sleep 1
  "$PY" "$SCRIPT_DIR/aivis-monitor.py" --url "http://127.0.0.1:$API_PORT" "$@"
}

cmd_update() {
  bash "$SCRIPT_DIR/aivis-update.sh" "$@"
}

cmd_logs() {
  if has_systemd; then
    say "로그를 표시합니다(최근 100줄 + 실시간). 종료하려면 Ctrl+C."
    sleep 1
    journalctl -u "$SERVICE_NAME" -n 100 -f 2>/dev/null || sudo journalctl -u "$SERVICE_NAME" -n 100 -f
  else
    if [ ! -f "$LOGFILE" ]; then
      warn "로그 파일이 없습니다: $LOGFILE  (아직 한 번도 시작하지 않았을 수 있습니다)"
      return 0
    fi
    say "로그: $LOGFILE (종료하려면 Ctrl+C)"
    sleep 1
    tail -n 100 -f "$LOGFILE"
  fi
}

primary_ip() {
  local ip=""
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  if [ -z "$ip" ] && command -v ip >/dev/null 2>&1; then
    ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')"
  fi
  [ -n "$ip" ] || ip="$(hostname 2>/dev/null)"
  [ -n "$ip" ] || ip="localhost"
  echo "$ip"
}

show_urls() {
  local ip; ip="$(primary_ip)"
  echo
  echo "==============================================================="
  echo "  ${C_B}사무실 PC 브라우저에서 아래 주소로 접속하세요${C_0}"
  echo "  (파이와 같은 네트워크에 연결되어 있어야 합니다)"
  echo "---------------------------------------------------------------"
  echo "   작업자 화면 (HMI)   ${C_B}http://$ip:$HMI_PORT${C_0}"
  echo "   관리자 대시보드      ${C_B}http://$ip:$DASHBOARD_PORT${C_0}"
  echo "   API/문서             ${C_B}http://$ip:$API_PORT${C_0}   (문서: /docs)"
  echo "---------------------------------------------------------------"
  echo "   로그인   아이디: ${C_B}admin${C_0}   비밀번호: ${C_B}$ADMIN_PW${C_0}"
  echo "   ${C_D}파이 화면에서 직접 볼 때는 http://localhost:$HMI_PORT${C_0}"
  echo "==============================================================="
  echo
}

usage() {
  cat <<TXT
AIVIS 통합 조작

  bash scripts/aivis.sh              번호 선택 메뉴(사람이 직접 조작할 때)
  bash scripts/aivis.sh <명령>       직접 실행(자동화용)

명령:
  start     시스템 시작
  stop      시스템 중지
  restart   재시작
  status    상태 보기(1회 요약)
  monitor   실시간 모니터(Ctrl+C 종료)
  update    프로그램 업데이트 (scripts/aivis-update.sh)
  logs      로그 보기(실시간)
  urls      접속 주소 표시
  menu      번호 선택 메뉴 강제 실행
  help      이 도움말

예)  bash scripts/aivis.sh urls
     bash scripts/aivis.sh update --restart
TXT
}

menu() {
  while true; do
    echo
    echo "==============================================================="
    echo "            ${C_B}AIVIS 검사 시스템 조작${C_0}"
    echo "==============================================================="
    echo "   1) 시스템 시작          2) 시스템 중지"
    echo "   3) 재시작               4) 상태 보기"
    echo "   5) 실시간 모니터        6) 프로그램 업데이트"
    echo "   7) 로그 보기            8) 접속 주소 표시"
    echo "   0) 종료"
    echo "==============================================================="
    printf "  번호를 입력하고 Enter: "
    local choice
    read -r choice || { echo; return 0; }
    case "$choice" in
      1) cmd_start ;;
      2) cmd_stop ;;
      3) cmd_restart ;;
      4) cmd_status ;;
      5) cmd_monitor ;;
      6) cmd_update ;;
      7) cmd_logs ;;
      8) show_urls ;;
      0|q|Q) say "종료합니다."; return 0 ;;
      "") ;;
      *) warn "1~8 또는 0 을 입력하세요." ;;
    esac
    if [ -t 0 ]; then
      printf "  ${C_D}계속하려면 Enter…${C_0}"
      read -r _ || true
    fi
  done
}

# --- 진입점 ------------------------------------------------------------------
CMD="${1:-}"
[ $# -gt 0 ] && shift
case "$CMD" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_restart ;;
  status)  cmd_status ;;
  monitor) cmd_monitor "$@" ;;
  update)  cmd_update "$@" ;;
  logs)    cmd_logs ;;
  urls)    show_urls ;;
  menu)    menu ;;
  help|-h|--help) usage ;;
  "")
    if [ -t 0 ]; then
      menu
    else
      # 파이프/스크립트에서 인자 없이 실행 — 메뉴 대신 사용법
      usage
      exit 0
    fi
    ;;
  *) err "알 수 없는 명령: $CMD"; echo; usage; exit 2 ;;
esac
