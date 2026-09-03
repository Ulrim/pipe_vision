#!/usr/bin/env bash
# =============================================================================
# AIVIS → 전남 AX 오픈플랫폼 데이터포털 정기 전송 (A안: 전남TP 제공 upload.sh 사용)
#
#   1) portal.cli run --no-upload : 검사결과 DB/이미지에서 증분 내보내기
#        → $AIVIS_PORTAL_EXPORT_DIR/runs/<회차>/{raw,processed,ai-analysis}/
#   2) 대기 중인 모든 회차 폴더를 데이터셋별 설정 파일로 upload.sh 전송
#        raw → jntp-raw.conf, processed → jntp-processed.conf, ai-analysis → jntp-ai-model.conf
#   3) 전송 성공(exit 0) 폴더는 삭제, 실패 폴더는 보존 → 다음 실행에서 자동 재시도
#      (포털은 같은 경로 재전송 시 최신 내용으로 갱신하므로 재시도가 안전하다)
#
# 사용:
#   bash scripts/jntp/aivis-portal-sync.sh                 # 3종 모두
#   bash scripts/jntp/aivis-portal-sync.sh --include-capture --include-calib   # 학습 촬영본 일괄(1회)
#   (추가 인자는 portal.cli run 에 그대로 전달된다)
# cron 예시(매일 02:17 KST, 매뉴얼 §3 과 동일 시각):
#   SHELL=/bin/bash
#   CRON_TZ=Asia/Seoul
#   17 2 * * * /opt/aivis/scripts/jntp/aivis-portal-sync.sh >> $HOME/jntp/aivis-portal-sync.log 2>&1
#
# 환경변수(선택):
#   JNTP_DIR=$HOME/jntp                 upload.sh + jntp-*.conf 위치(매뉴얼 §2.1)
#   AIVIS_PORTAL_EXPORT_DIR=/data/portal_export   내보내기 작업 폴더
#   AIVIS_DATAOPS_DIR=<repo>/services/data-ops    portal.cli 위치
#   AIVIS_PYTHON=python3                 data-ops 를 실행할 파이썬(venv 권장)
#   AIVIS_PORTAL_DATASETS="raw processed ai-analysis"   전송할 데이터셋
#   DATABASE_URL / AIVIS_IMAGES_DIR / AIVIS_DATASET_DIR / AIVIS_REPORTS_DIR  (portal.cli 가 읽음)
# B안(API 직접 연계)은 이 스크립트 대신 `python -m portal.cli run --conf-raw ...` 를 쓴다.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JNTP_DIR="${JNTP_DIR:-$HOME/jntp}"
EXPORT_DIR="${AIVIS_PORTAL_EXPORT_DIR:-/data/portal_export}"
DATAOPS_DIR="${AIVIS_DATAOPS_DIR:-$(cd "$SCRIPT_DIR/../../services/data-ops" && pwd)}"
PYTHON="${AIVIS_PYTHON:-python3}"
DATASETS="${AIVIS_PORTAL_DATASETS:-raw processed ai-analysis}"
UPLOAD_SH="${JNTP_UPLOAD_SH:-$JNTP_DIR/upload.sh}"
[ -x "$UPLOAD_SH" ] || UPLOAD_SH="$SCRIPT_DIR/upload.sh"

ts() { date "+%Y-%m-%dT%H:%M:%S%z"; }
log() { echo "[$(ts)] [portal-sync] $*" >&2; }

conf_for() {
  case "$1" in
  raw) echo "$JNTP_DIR/jntp-raw.conf" ;;
  processed) echo "$JNTP_DIR/jntp-processed.conf" ;;
  ai-analysis) echo "$JNTP_DIR/jntp-ai-model.conf" ;;
  *) return 1 ;;
  esac
}

# --- 1) 증분 내보내기 (실패해도 대기분 전송은 계속) ---
log "내보내기 시작: $EXPORT_DIR"
export AIVIS_PORTAL_EXPORT_DIR="$EXPORT_DIR"
if ! (cd "$DATAOPS_DIR" && PYTHONPATH="${PYTHONPATH:-}:../api" "$PYTHON" -m portal.cli run \
  --out "$EXPORT_DIR" --no-upload --dataset all "$@" >/dev/null); then
  log "내보내기 실패 — 대기분 전송만 진행합니다"
fi

# --- 2) 대기 회차 전송 (오래된 회차부터) ---
fail=0
shopt -s nullglob
for run_dir in "$EXPORT_DIR"/runs/*/; do
  run_dir="${run_dir%/}"
  for ds in $DATASETS; do
    d="$run_dir/$ds"
    [ -d "$d" ] || continue
    if [ -z "$(find "$d" -type f -print -quit)" ]; then
      rm -rf "$d"                       # 빈 데이터셋 폴더(전송할 것 없음) 정리
      continue
    fi
    conf="$(conf_for "$ds")"
    if [ ! -f "$conf" ]; then
      log "설정 파일 없음: $conf — $ds 건너뜀"
      fail=1
      continue
    fi
    log "전송: $(basename "$run_dir")/$ds"
    if JNTP_CONF="$conf" "$UPLOAD_SH" "$d"; then
      rm -rf "$d"
    else
      log "전송 실패/제외 발생: $d 보존(다음 실행에서 재시도)"
      fail=1
    fi
  done
  rmdir "$run_dir" 2>/dev/null || true
done

if [ "$fail" -ne 0 ]; then
  log "완료 — 일부 실패(보존된 회차 폴더 확인: $EXPORT_DIR/runs)"
  exit 1
fi
log "완료 — 대기분 없음"
