#!/usr/bin/env bash
# AIVIS 검사 이미지 정리 (디스크가 가득 찼을 때 즉시 복구용)
#
# 왜 필요한가: 검사 1건마다 이미지가 쌓이는데 예전 버전에는 지우는 규칙이
# 없었다. 현장 파이에서 57GB 카드가 여유 401MB 까지 몰려 검사 저장·업데이트가
# 전부 실패했다. 새 버전은 스스로 정리하지만, **이미 가득 찬 장비는 업데이트를
# 받을 공간조차 없다.** 이 스크립트로 먼저 공간을 비운 뒤 업데이트한다.
#
# 사용법:
#   bash scripts/aivis-clean-images.sh          # 무엇을 지울지 보여주기만 함
#   bash scripts/aivis-clean-images.sh --yes    # 실제로 삭제
#
# 지우는 순서(가치가 낮은 것부터): 원본(raw) → 양품 판정(result *_OK) →
# 불량 판정(result *_NG, 90일 초과분). 재확인본(review/)은 재학습 자산이라
# 이 스크립트에서는 건드리지 않는다.
set -euo pipefail

IMAGES_DIR="${AIVIS_IMAGES_DIR:-/var/lib/aivis/images}"
APPLY="no"
[ "${1:-}" = "--yes" ] && APPLY="yes"

SUDO=""
[ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"

if [ ! -d "$IMAGES_DIR" ]; then
  echo "이미지 폴더가 없습니다: $IMAGES_DIR" >&2
  echo "다른 위치라면 AIVIS_IMAGES_DIR 로 알려주세요." >&2
  exit 1
fi

human() { du -sh "$1" 2>/dev/null | awk '{print $1}'; }

echo "== 현재 상태 =="
df -h "$IMAGES_DIR" | awk 'NR==1||NR==2'
for d in raw result review; do
  [ -d "$IMAGES_DIR/$d" ] && printf "  %-7s %s\n" "$d" "$(human "$IMAGES_DIR/$d")"
done
echo

# 지울 대상을 단계별로 모은다. 각 단계는 "설명|find 조건" 형태.
run_step() {
  local desc="$1"; shift
  local count size
  count="$("$@" -printf . 2>/dev/null | wc -c)"
  [ "$count" -eq 0 ] && return 0
  size="$("$@" -printf '%s\n' 2>/dev/null | awk '{s+=$1} END{
      if (s >= 1073741824) printf "%.1fGB", s/1073741824; else printf "%.0fMB", s/1048576}')"
  echo "  ${desc}: ${count}개 (약 ${size})"
  if [ "$APPLY" = "yes" ]; then
    "$@" -delete 2>/dev/null || true
  fi
}

echo "== 정리 대상 =="
run_step "원본 이미지 전체" $SUDO find "$IMAGES_DIR/raw" -type f -name '*.jpg'
run_step "양품 판정 이미지(7일 초과)" $SUDO find "$IMAGES_DIR/result" -type f -name '*_OK.jpg' -mtime +7
run_step "불량 판정 이미지(90일 초과)" $SUDO find "$IMAGES_DIR/result" -type f -name '*_NG.jpg' -mtime +90
echo

if [ "$APPLY" != "yes" ]; then
  echo "지금은 **보여주기만** 했습니다. 실제로 지우려면:"
  echo "  bash scripts/aivis-clean-images.sh --yes"
  exit 0
fi

echo "== 정리 후 =="
df -h "$IMAGES_DIR" | awk 'NR==1||NR==2'
echo
echo "검사 이력(DB)과 통계는 그대로 남아 있습니다. 이미지만 지워졌습니다."
