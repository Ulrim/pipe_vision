#!/usr/bin/env bash

set -euo pipefail

export PATH=/usr/local/bin:/usr/bin:/bin

_env_api_base="${JNTP_API_BASE:-}"
_env_upload_code="${JNTP_UPLOAD_CODE:-}"

CONF_FILE="${JNTP_CONF:-$(cd "$(dirname "$0")" && pwd)/jntp.conf}"
if [ -f "$CONF_FILE" ]; then
  # shellcheck source=/dev/null
  . "$CONF_FILE"
fi

API_BASE="${_env_api_base:-${JNTP_API_BASE:-http://localhost:8000}}"
CODE="${_env_upload_code:-${JNTP_UPLOAD_CODE:?업로드 코드가 없습니다 — jntp.conf에 JNTP_UPLOAD_CODE를 넣거나 환경변수로 지정하세요}}"
ROOT="${1:?업로드할 폴더 경로를 인자로 주세요}"

ROOT="$(cd "${ROOT%/}" && pwd)"
LOCK_DIR="${JNTP_LOCK_DIR:-/tmp/jntp-upload-${CODE}.lock}"

BATCH="${JNTP_BATCH_SIZE:-300}"

MAX_TIME="${JNTP_MAX_TIME:-600}"
RETRY="${JNTP_RETRY:-3}"
RETRY_DELAY="${JNTP_RETRY_DELAY:-30}"

ts() { date "+%Y-%m-%dT%H:%M:%S%z"; }

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    echo $$ >"$LOCK_DIR/pid"
    return 0
  fi
  local owner
  owner="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  if [ -n "$owner" ] && ! kill -0 "$owner" 2>/dev/null; then
    rm -rf "$LOCK_DIR"
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      echo $$ >"$LOCK_DIR/pid"
      return 0
    fi
  fi
  return 1
}

if ! acquire_lock; then
  echo "[$(ts)] 이전 업로드가 아직 진행 중입니다 — 이번 실행은 건너뜁니다" >&2
  exit 0
fi

CURL_CONF="$(mktemp "${TMPDIR:-/tmp}/jntp-upload.XXXXXX")"
trap 'rm -rf "$LOCK_DIR"; rm -f "$CURL_CONF"' EXIT

unsafe_name() {
  case "$1" in
  *'"'* | *'\'* | *';'*) return 0 ;;
  esac
  return 1
}

sort_paths() {
  if printf 'b\0a\0' | LC_ALL=C sort -z >/dev/null 2>&1; then
    LC_ALL=C sort -z
  else
    tr '\0' '\n' | LC_ALL=C sort | tr '\n' '\0'
  fi
}

paths=()
skipped_empty=0
skipped_name=0

while IFS= read -r -d '' path; do
  if [ ! -s "$path" ]; then
    skipped_empty=$((skipped_empty + 1))
    continue
  fi
  if unsafe_name "$path"; then
    skipped_name=$((skipped_name + 1))
    echo "[$(ts)] 제외(파일명): ${path#"$ROOT"/}" >&2
    continue
  fi
  paths+=("$path")
done < <(
  find "$ROOT" -type f -not -path '*/.*' -not -path '*/__MACOSX/*' -print0 |
    sort_paths
)

total=${#paths[@]}

if [ "$skipped_empty" -gt 0 ]; then
  echo "[$(ts)] 빈 파일 ${skipped_empty}건은 제외했습니다" >&2
fi

if [ "$total" -eq 0 ]; then
  echo "[$(ts)] 올릴 파일이 없습니다: $ROOT" >&2
  exit 1
fi

batches=$(((total + BATCH - 1) / BATCH))

list_digest() {
  printf '%s\n' "${paths[@]}" |
    { shasum -a 256 2>/dev/null || sha256sum; } | cut -c1-8
}

RUN_ID="$(date +%Y%m%dT%H%M%S)-$(list_digest)"

echo "[$(ts)] ${total}건을 ${batches}회로 나눠 보냅니다" >&2

post_batch() {
  curl --fail-with-body -sS -X POST "${API_BASE}/dataset-uploads" \
    --http1.1 \
    --max-time "$MAX_TIME" \
    --retry "$RETRY" --retry-delay "$RETRY_DELAY" \
    -H "X-Dataset-Code: ${CODE}" \
    -H "X-Upload-Run: ${RUN_ID}" \
    --config "$CURL_CONF"
}

rejected=0
report_rejected() {
  local body="$1" entry name reason rest
  case "$body" in
  *'"rejected":'*) rest="${body#*\"rejected\":}" ;;
  *) return 0 ;;
  esac
  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    name="${entry#\"fileName\":\"}"
    name="${name%%\",\"reason\":*}"
    reason="${entry##*\"reason\":\"}"
    reason="${reason%\"}"
    echo "[$(ts)] 제외(서버): ${name} — ${reason}" >&2
    rejected=$((rejected + 1))
  done < <(
    printf '%s' "$rest" |
      sed 's/"fileName": *"/"fileName":"/g; s/", *"reason": *"/","reason":"/g' |
      grep -o '"fileName":"[^"]*","reason":"[^"]*"' || true
  )
}

write_batch_config() {
  local i end path rel
  : >"$CURL_CONF"
  end=$(($1 + BATCH))
  [ "$end" -gt "$total" ] && end=$total
  for ((i = $1; i < end; i++)); do
    path="${paths[i]}"
    rel="${path#"$ROOT"/}"
    printf 'form = "files=@\\"%s\\";filename=\\"%s\\""\n' "$path" "$rel" >>"$CURL_CONF"
  done
}

version=""
registered=0
n=0
while [ "$n" -lt "$batches" ]; do
  write_batch_config $((n * BATCH))
  if ! body="$(post_batch)"; then
    echo "[$(ts)] $((n + 1))/${batches} 묶음 전송 실패 — $(printf '%s' "$body" | tr '\n' ' ' | cut -c1-500)" >&2
    echo "[$(ts)] 여기까지 보낸 $((registered))건은 버전 ${version:-?}에 등록되어 있습니다" >&2
    exit 1
  fi
  report_rejected "$body"
  version="$(printf '%s' "$body" | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p')"
  accepted="$(printf '%s' "$body" | sed -n 's/.*"acceptedCount": *\([0-9]*\).*/\1/p')"
  registered=$((registered + ${accepted:-0}))
  n=$((n + 1))
  echo "[$(ts)] ${n}/${batches} 묶음 — 누적 ${registered}건" >&2
done

if [ "$rejected" -gt 0 ] || [ "$skipped_name" -gt 0 ]; then
  echo "[$(ts)] 완료 — 버전 ${version:-?}, 등록 ${registered}건, 제외 $((rejected + skipped_name))건" >&2
  exit 1
fi

echo "[$(ts)] 완료 — 버전 ${version:-?}, 등록 ${registered}건" >&2
