#!/usr/bin/env python3
"""AIVIS 파이 터미널 실시간 모니터 (CLAUDE.md §4 런타임 토폴로지 운영 보조).

라즈베리파이 7인치 화면이나 SSH 창에서 시스템 상태를 한국어로 보여준다.
표준 라이브러리만 사용한다(새 의존성 금지) — 파이에 아무것도 더 설치할 필요 없음.

사용:
    python3 scripts/aivis-monitor.py              # 2초 주기 실시간(Ctrl+C 종료)
    python3 scripts/aivis-monitor.py --once       # 1회 출력 후 종료
    python3 scripts/aivis-monitor.py --interval 5 --url http://127.0.0.1:8000

핵심 원칙:
  · API 가 죽어 있어도 유용해야 한다. 서비스가 죽었을 때가 모니터가 가장 필요한
    순간이므로, 연결 실패 시에도 파이에서 직접 읽을 수 있는 지표(CPU 온도/로드/
    디스크)를 계속 표시한다.
  · 상태는 색 + 기호 + 한국어 3중 표기(적녹색약 고려). 색이 안 나와도 판독 가능.
  · 숫자 필드는 null 일 수 있다 → "측정불가" 로 표기하고 0 으로 대체하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import urllib.error
import urllib.request

DEFAULT_URL = os.environ.get("AIVIS_API_URL", "http://127.0.0.1:8000")
DEFAULT_USER = os.environ.get("AIVIS_MONITOR_USER", "admin")
DEFAULT_PASSWORD = os.environ.get("AIVIS_ADMIN_PASSWORD", "aivis1234")

# --- ANSI (지원 안 될 수 있으므로 문자 표기가 항상 함께 간다) ----------------
USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def GREEN(s: str) -> str:
    return c("32", s)


def YELLOW(s: str) -> str:
    return c("33", s)


def RED(s: str) -> str:
    return c("31;1", s)


def BOLD(s: str) -> str:
    return c("1", s)


def DIM(s: str) -> str:
    return c("2", s)

# 상태 3중 표기: (기호, 한국어, 색함수)
ST_OK = ("[O]", "정상", GREEN)
ST_WARN = ("[!]", "주의", YELLOW)
ST_BAD = ("[X]", "위험", RED)
ST_UNK = ("[?]", "확인불가", DIM)


def mark(state: tuple, extra: str = "") -> str:
    sym, word, color = state
    return color(f"{sym} {word}") + (f" {extra}" if extra else "")


# --- 폭 계산(한글은 2칸, ANSI 색코드는 0칸) — 표 정렬이 깨지지 않게 -----------
ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def vlen(text: str) -> int:
    plain = ANSI_RE.sub("", text)
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in plain)


def lpad(text: str, width: int) -> str:
    return " " * max(0, width - vlen(text)) + text


def rpad(text: str, width: int) -> str:
    return text + " " * max(0, width - vlen(text))


# =============================================================================
# 로컬 지표 (API 없이도 읽을 수 있는 것들 — 서비스가 죽었을 때의 생명줄)
# =============================================================================
def local_cpu_temp_c():
    for path in (
        "/sys/class/thermal/thermal_zone0/temp",
        "/sys/devices/virtual/thermal/thermal_zone0/temp",
    ):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
            val = float(raw)
            return val / 1000.0 if val > 200 else val
        except (OSError, ValueError):
            continue
    return None


def local_load():
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):
        return None


def local_disk(path: str):
    """(사용률%, 사용GB, 전체GB) 또는 (None, None, None)."""
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None, None, None
    gb = 1024.0 ** 3
    used = usage.used / gb
    total = usage.total / gb
    pct = (usage.used / usage.total * 100.0) if usage.total else None
    return pct, used, total


_CPU_SAMPLE = {"total": None, "idle": None}


def _read_cpu_jiffies():
    try:
        with open("/proc/stat", "r", encoding="utf-8") as fh:
            parts = fh.readline().split()
    except OSError:
        return None, None
    if not parts or parts[0] != "cpu" or len(parts) < 5:
        return None, None
    try:
        values = [float(v) for v in parts[1:]]
    except ValueError:
        return None, None
    return sum(values), values[3]  # (total, idle)


def local_cpu_percent():
    """직전 폴링과의 /proc/stat 차분으로 CPU 사용률 산출(API 없이도 표시)."""
    total, idle = _read_cpu_jiffies()
    if total is None:
        return None
    prev_total, prev_idle = _CPU_SAMPLE["total"], _CPU_SAMPLE["idle"]
    _CPU_SAMPLE["total"], _CPU_SAMPLE["idle"] = total, idle
    if prev_total is None:
        time.sleep(0.15)  # 첫 호출: 짧게 두 번째 표본을 뜬다
        total2, idle2 = _read_cpu_jiffies()
        if total2 is None:
            return None
        prev_total, prev_idle = total, idle
        total, idle = total2, idle2
        _CPU_SAMPLE["total"], _CPU_SAMPLE["idle"] = total, idle
    d_total = total - prev_total
    d_idle = idle - prev_idle
    if d_total <= 0:
        return None
    return max(0.0, min(100.0, (1.0 - d_idle / d_total) * 100.0))


def local_mem():
    """(사용률%, 사용MB, 전체MB) — /proc/meminfo 기반. 없으면 None."""
    try:
        info = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                info[key] = float(rest.strip().split()[0])  # kB
    except (OSError, ValueError, IndexError):
        return None, None, None
    total = info.get("MemTotal")
    avail = info.get("MemAvailable")
    if not total or avail is None:
        return None, None, None
    used_mb = (total - avail) / 1024.0
    total_mb = total / 1024.0
    return (used_mb / total_mb * 100.0 if total_mb else None), used_mb, total_mb


# =============================================================================
# API 클라이언트 (urllib 만 사용)
# =============================================================================
class ApiClient:
    def __init__(self, base_url: str, user: str, password: str, timeout: float = 4.0):
        self.base = base_url.rstrip("/")
        self.user = user
        self.password = password
        self.timeout = timeout
        self.token = None
        self.last_error = None

    def _request(self, path: str, method="GET", body=None, auth=True):
        url = f"{self.base}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if auth and self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")

    def login(self) -> bool:
        try:
            payload = self._request(
                "/auth/login",
                method="POST",
                body={"username": self.user, "password": self.password},
                auth=False,
            )
            self.token = payload.get("access_token")
            return bool(self.token)
        except urllib.error.HTTPError as exc:
            self.last_error = f"로그인 거부(HTTP {exc.code}) — 계정/비밀번호 확인"
        except urllib.error.URLError:
            self.last_error = "연결 실패 — API 가 꺼져 있거나 주소/포트가 다릅니다"
        except Exception as exc:  # 타임아웃/JSON 파싱 등
            self.last_error = f"연결 실패({type(exc).__name__})"
        return False

    def fetch_status(self):
        """(data, error) — 성공 시 (dict, None), 실패 시 (None, 사유)."""
        if not self.token and not self.login():
            return None, self.last_error or "로그인 실패"
        for attempt in (1, 2):
            try:
                return self._request("/system/status"), None
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 1:
                    self.token = None
                    if not self.login():
                        return None, self.last_error or "재로그인 실패"
                    continue
                if exc.code == 404:
                    return None, "API 에 /system/status 없음(구버전) — 업데이트 필요"
                if exc.code == 401:
                    return None, "인증 실패(HTTP 401) — 계정/비밀번호 확인"
                if exc.code == 403:
                    return None, "권한 없음(HTTP 403) — operator 이상 계정 필요"
                return None, f"HTTP {exc.code}"
            except urllib.error.URLError as exc:
                return None, f"연결 실패({getattr(exc, 'reason', exc)})"
            except Exception as exc:
                return None, f"오류: {type(exc).__name__}"
        return None, "알 수 없는 오류"


# =============================================================================
# 표시 헬퍼
# =============================================================================
UNKNOWN = DIM("측정불가")


def num(value, fmt="{:.1f}", suffix=""):
    if value is None:
        return UNKNOWN
    try:
        return fmt.format(float(value)) + suffix
    except (TypeError, ValueError):
        return UNKNOWN


def bar(pct, width=20):
    """텍스트 게이지. 색이 없어도 채움 정도로 판독 가능."""
    if pct is None:
        return "[" + "?" * width + "]"
    pct = max(0.0, min(100.0, float(pct)))
    filled = int(round(pct / 100.0 * width))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def temp_state(t):
    if t is None:
        return ST_UNK
    if t >= 80:
        return ST_BAD
    if t >= 70:
        return ST_WARN
    return ST_OK


def disk_state(pct):
    if pct is None:
        return ST_UNK
    if pct >= 95:
        return ST_BAD
    if pct >= 85:
        return ST_WARN
    return ST_OK


def pct_state(pct, warn=85.0, bad=95.0):
    if pct is None:
        return ST_UNK
    if pct >= bad:
        return ST_BAD
    if pct >= warn:
        return ST_WARN
    return ST_OK


def service_state(value):
    """백엔드의 up/stale/down 문자열 → (기호, 한국어, 색) 3중 표기 상태."""
    if value == "up":
        return ("[O]", "정상", GREEN)
    if value in ("stale", "degraded"):
        return ("[!]", "응답지연", YELLOW)
    if value == "down":
        return ("[X]", "정지", RED)
    return ("[?]", "확인불가", DIM)


def short_time(value):
    if not value:
        return UNKNOWN
    text = str(value)
    return text[11:19] if len(text) >= 19 and text[10:11] in ("T", " ") else text


def get(dct, *keys, default=None):
    """중첩 dict 안전 조회 — 필드가 없거나 null 이어도 죽지 않는다."""
    cur = dct
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


# =============================================================================
# 화면 그리기
# =============================================================================
LINE = "-" * 62


def render(data, error, base_url, images_dir):
    out = []
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    out.append(BOLD("  AIVIS 현장 모니터") + DIM(f"   {now}"))
    out.append(DIM(f"  API: {base_url}    (Ctrl+C 로 종료)"))
    out.append(LINE)

    if error and ("401" in error or "403" in error or "로그인" in error):
        # 서비스는 살아 있는데 계정 문제 — 조치가 다르므로 따로 안내한다.
        out.append("  " + YELLOW("[!] 모니터 로그인 실패") + f"  ({error})")
        out.append("  " + "→ 계정/비밀번호를 지정해 다시 실행하세요:")
        out.append("     python3 scripts/aivis-monitor.py --user admin --password <비밀번호>")
        out.append("     " + DIM("(환경변수 AIVIS_ADMIN_PASSWORD 로도 지정 가능)"))
    elif error:
        out.append("  " + RED("[X] API 응답 없음") + f"  ({error})")
        out.append("  " + YELLOW("→ 검사·저장이 멈췄을 수 있습니다. 아래 순서로 확인하세요:"))
        out.append("     1) bash scripts/aivis.sh status     (서비스 상태)")
        out.append("     2) bash scripts/aivis.sh start      (꺼져 있으면 시작)")
        out.append("     3) bash scripts/aivis.sh logs       (오류 원인 보기)")
    if error:
        out.append("  " + DIM("아래는 파이에서 직접 읽은 값이라 API 없이도 표시됩니다."))
        out.append(LINE)

    # --- 시스템(파이 하드웨어) ---
    sys_d = data.get("system") if isinstance(data, dict) else None
    temp = get(sys_d, "cpu_temp_c") if sys_d else None
    if temp is None:
        temp = local_cpu_temp_c()
    cpu_pct = get(sys_d, "cpu_percent") if sys_d else None
    if cpu_pct is None:
        cpu_pct = local_cpu_percent()
    load1 = get(sys_d, "load_1m") if sys_d else None
    if load1 is None:
        load1 = local_load()
    mem_pct = get(sys_d, "mem_percent") if sys_d else None
    mem_used = get(sys_d, "mem_used_mb") if sys_d else None
    mem_total = get(sys_d, "mem_total_mb") if sys_d else None
    if mem_pct is None and mem_used is None:
        mem_pct, mem_used, mem_total = local_mem()
    disk_pct = get(sys_d, "disk_percent") if sys_d else None
    disk_used = get(sys_d, "disk_used_gb") if sys_d else None
    disk_total = get(sys_d, "disk_total_gb") if sys_d else None
    if disk_pct is None and disk_used is None:
        disk_pct, disk_used, disk_total = local_disk(images_dir)
    throttled = get(sys_d, "throttled") if sys_d else None

    out.append(BOLD("  [ 라즈베리파이 상태 ]"))
    out.append(
        f"   CPU 온도   {lpad(num(temp, '{:.1f}', ' C'), 10)}   {mark(temp_state(temp))}"
        + ("   " + YELLOW("냉각/환기 확인") if temp is not None and temp >= 70 else "")
    )
    out.append(
        f"   CPU 사용   {lpad(num(cpu_pct, '{:.0f}', ' %'), 10)}   {bar(cpu_pct)}"
        f"   부하 {num(load1, '{:.2f}')}"
    )
    mem_txt = (
        f"{num(mem_used, '{:.0f}')}/{num(mem_total, '{:.0f}')} MB"
        if mem_used is not None
        else UNKNOWN
    )
    out.append(
        f"   메모리     {lpad(num(mem_pct, '{:.0f}', ' %'), 10)}   {bar(mem_pct)}   {mem_txt}"
    )
    disk_txt = (
        f"{num(disk_used, '{:.1f}')}/{num(disk_total, '{:.1f}')} GB"
        if disk_used is not None
        else UNKNOWN
    )
    out.append(
        f"   디스크     {lpad(num(disk_pct, '{:.0f}', ' %'), 10)}   {bar(disk_pct)}   {disk_txt}"
        f"   {mark(disk_state(disk_pct))}"
    )
    if disk_pct is not None and disk_pct >= 85:
        out.append("   " + YELLOW("→ 저장공간 부족: 오래된 검사 이미지를 정리하거나 외장 저장소로 옮기세요."))
    if throttled is True:
        out.append("   " + RED("[X] 전원/발열 스로틀 발생") + " — 정품 전원어댑터와 방열 상태를 확인하세요.")
    out.append("")

    # --- 서비스 ---
    out.append(BOLD("  [ 서비스 ]"))
    if data:
        db_val = get(data, "services", "db")
        worker_val = get(data, "services", "worker")
        seen = get(data, "services", "worker_last_seen_s")
        db_state = service_state(db_val)
        wk_state = service_state(worker_val)
        out.append(f"   API 서버     {mark(ST_OK)}   (응답 정상)")
        out.append(f"   데이터베이스 {mark(db_state)}")
        seen_txt = f"마지막 신호 {num(seen, '{:.0f}', '초 전')}" if seen is not None else DIM("신호 없음")
        out.append(f"   검사 워커    {rpad(mark(wk_state), 12)}   {seen_txt}")
        if worker_val == "down":
            out.append("   " + YELLOW("→ 카메라/워커 정지: bash scripts/aivis.sh restart 로 재시작하세요."))
    else:
        out.append(f"   API 서버     {mark(ST_BAD)}   (응답 없음)")
        out.append(f"   데이터베이스 {mark(ST_UNK)}")
        out.append(f"   검사 워커    {mark(ST_UNK)}")
    out.append("")

    # --- 검사 실적 ---
    out.append(BOLD("  [ 검사 실적 ]"))
    if data:
        order = data.get("active_order") or {}
        item = order.get("item_code") or "-"
        lot = order.get("lot") or "-"
        wo = order.get("work_order") or "-"
        out.append(f"   현재 오더   품목 {item}   LOT {lot}   작업지시 {wo}")
        for label, key in (("최근 1시간", "last_hour"), ("오늘", "today")):
            total = get(data, "inspection", key, "total")
            ng = get(data, "inspection", key, "ng")
            rate = get(data, "inspection", key, "ng_rate_pct")
            out.append(
                f"   {rpad(label, 12)} 검사 {lpad(num(total, '{:.0f}', '개'), 9)}"
                f"   NG {lpad(num(ng, '{:.0f}', '개'), 8)}"
                f"   불량률 {lpad(num(rate, '{:.2f}', ' %'), 9)}"
            )
        avg = get(data, "inspection", "avg_proc_time_ms")
        p95 = get(data, "inspection", "p95_proc_time_ms")
        speed_state = ST_UNK if p95 is None else (ST_OK if p95 <= 300 else ST_WARN)
        out.append(
            f"   처리속도   평균 {num(avg, '{:.0f}', ' ms')}   p95 {num(p95, '{:.0f}', ' ms')}"
            f"   {mark(speed_state)}  " + DIM("(목표 300ms 이하)")
        )
        out.append(f"   마지막 검사 {short_time(get(data, 'inspection', 'last_inspected_at'))}")
        pending = get(data, "inspection", "mes_pending")
        mes_state = ST_UNK if pending is None else (ST_OK if pending == 0 else ST_WARN)
        out.append(f"   MES 미전송  {lpad(num(pending, '{:.0f}', '건'), 9)}   {mark(mes_state)}")
    else:
        out.append("   " + DIM("API 연결 후 표시됩니다."))
    out.append("")

    # --- 최근 오류 ---
    errors = (data or {}).get("recent_errors") or []
    out.append(BOLD("  [ 최근 오류 ]"))
    if not data:
        out.append("   " + DIM("API 연결 후 표시됩니다."))
    elif not errors:
        out.append("   " + GREEN("없음"))
    else:
        for item in errors[:5]:
            if not isinstance(item, dict):
                continue
            msg = str(item.get("message") or "")[:46]
            out.append(f"   {short_time(item.get('ts'))}  {msg}")
    out.append(LINE)
    return "\n".join(out)


def clear_screen():
    # ANSI: 커서 홈 + 화면 지우기(스크롤백 보존). 지원 안 하면 개행으로 대체됨.
    sys.stdout.write("\033[H\033[2J")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AIVIS 파이 터미널 실시간 모니터 (표준 라이브러리만 사용)"
    )
    parser.add_argument("--url", default=DEFAULT_URL, help=f"API 주소 (기본 {DEFAULT_URL})")
    parser.add_argument("--user", default=DEFAULT_USER, help="로그인 계정 (기본 admin)")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="로그인 비밀번호")
    parser.add_argument("--interval", type=float, default=2.0, help="갱신 주기(초, 기본 2)")
    parser.add_argument("--once", action="store_true", help="1회 출력 후 종료")
    parser.add_argument(
        "--images-dir",
        default=os.environ.get("AIVIS_IMAGES_DIR")
        or os.path.join(os.environ.get("AIVIS_HOME", "/var/lib/aivis"), "images"),
        help="디스크 사용량을 볼 경로(기본: AIVIS_HOME/images)",
    )
    args = parser.parse_args()

    images_dir = args.images_dir
    if not os.path.isdir(images_dir):
        images_dir = "/"  # 데이터 폴더가 아직 없으면 루트 파티션으로 대체

    client = ApiClient(args.url, args.user, args.password)
    interval = max(0.5, args.interval)

    try:
        while True:
            data, error = client.fetch_status()
            screen = render(data, error, args.url, images_dir)
            if not args.once:
                clear_screen()
            print(screen, flush=True)
            if args.once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n모니터를 종료합니다.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
