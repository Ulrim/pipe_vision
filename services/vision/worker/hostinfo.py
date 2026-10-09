"""이 파이 자신의 상태(온도·부하·메모리·디스크·전원) — 하트비트에 싣는다.

파이가 여러 대면(2026-10-09) API 의 `/system/status.system` 은 **API 가 도는
1호기**만 보여준다. 2호기가 과열·디스크 꽉 참·전원 부족이어도 사무실 화면은
모른다. 그래서 각 워커가 자기 상태를 하트비트로 보내고, 대시보드 '실시간 현황'
이 스테이션마다 보여준다.

API 쪽 수집(routers/system.py)과 같은 원칙:
- 표준 라이브러리만(psutil 을 파이에 깔지 않는다).
- 어떤 읽기 실패도 밖으로 던지지 않는다 → 해당 값 None.
- subprocess(vcgencmd) 금지 — 검사 루프를 붙잡을 수 있다.

CPU 사용률은 /proc/stat 의 **직전 호출 대비 변화량**으로 구한다(sleep 없음).
첫 호출은 기준이 없으니 None. 하트비트마다(1.5s) 부르면 그 간격의 평균이다.
수집 자체는 `min_interval_s`(기본 5s) 마다 한 번만 하고 그 사이엔 캐시를 준다
— 파일 다섯 개 읽기라도 사이클마다 할 이유가 없다.
"""
from __future__ import annotations

import os
import shutil
import time
from typing import Callable, Optional

CPU_TEMP_PATH = "/sys/class/thermal/thermal_zone0/temp"
MEMINFO_PATH = "/proc/meminfo"
STAT_PATH = "/proc/stat"
THROTTLED_PATH = "/sys/devices/platform/soc/soc:firmware/get_throttled"


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _cpu_temp_c(read=_read) -> Optional[float]:
    try:
        return round(int(read(CPU_TEMP_PATH).strip()) / 1000.0, 1)
    except Exception:
        return None


def _load_1m() -> Optional[float]:
    try:
        return round(float(os.getloadavg()[0]), 2)
    except Exception:
        return None


def _mem_percent(read=_read) -> Optional[float]:
    try:
        vals: dict[str, float] = {}
        for line in read(MEMINFO_PATH).splitlines():
            k, _, rest = line.partition(":")
            if k in ("MemTotal", "MemAvailable"):
                vals[k] = float(rest.strip().split()[0])
        total, avail = vals["MemTotal"], vals["MemAvailable"]
        if total <= 0:
            return None
        return round(max(0.0, total - avail) / total * 100.0, 1)
    except Exception:
        return None


def _disk(path: str) -> tuple[Optional[float], Optional[float]]:
    """(사용률 %, 남은 GB). 이미지가 쌓이는 볼륨 기준(없으면 /)."""
    try:
        target = path if path and os.path.isdir(path) else "/"
        u = shutil.disk_usage(target)
        pct = (u.used / u.total * 100.0) if u.total else 0.0
        return round(pct, 1), round(u.free / 1024.0 ** 3, 1)
    except Exception:
        return None, None


def _throttled(read=_read) -> Optional[bool]:
    try:
        raw = read(THROTTLED_PATH).strip()
        return int(raw, 16 if raw.lower().startswith("0x") else 10) != 0
    except Exception:
        return None


def _cpu_times(read=_read) -> Optional[tuple[int, int]]:
    """(idle+iowait, total) jiffies. 실패 시 None."""
    try:
        first = read(STAT_PATH).splitlines()[0].split()
        if first[0] != "cpu":
            return None
        nums = [int(x) for x in first[1:]]
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
        return idle, sum(nums)
    except Exception:
        return None


class HostInfo:
    """하트비트용 자기 상태 수집기. `snapshot()` 은 dict(JSON 직렬화 가능)."""

    def __init__(
        self,
        images_dir: str = "/",
        *,
        min_interval_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        read: Callable[[str], str] = _read,
    ) -> None:
        self.images_dir = images_dir
        self.min_interval_s = min_interval_s
        self._clock = clock
        self._read = read
        self._last_at: Optional[float] = None
        self._cached: Optional[dict] = None
        self._prev_cpu: Optional[tuple[int, int]] = _cpu_times(read)

    def _cpu_percent(self) -> Optional[float]:
        cur = _cpu_times(self._read)
        prev, self._prev_cpu = self._prev_cpu, cur
        if cur is None or prev is None:
            return None
        d_total = cur[1] - prev[1]
        d_idle = cur[0] - prev[0]
        if d_total <= 0:
            return None
        return round(max(0.0, min(100.0, (1.0 - d_idle / d_total) * 100.0)), 1)

    def snapshot(self) -> dict:
        now = self._clock()
        if (
            self._cached is not None
            and self._last_at is not None
            and now - self._last_at < self.min_interval_s
        ):
            return self._cached
        disk_pct, disk_free = _disk(self.images_dir)
        snap = {
            "cpu_temp_c": _cpu_temp_c(self._read),
            "cpu_percent": self._cpu_percent(),
            "load_1m": _load_1m(),
            "mem_percent": _mem_percent(self._read),
            "disk_percent": disk_pct,
            "disk_free_gb": disk_free,
            "throttled": _throttled(self._read),
        }
        self._cached, self._last_at = snap, now
        return snap
