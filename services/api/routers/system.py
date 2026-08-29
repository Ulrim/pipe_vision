"""시스템 모니터링 라우터 (CLAUDE.md §5 M15, §4 런타임 토폴로지).

현장(라즈베리파이 단일 호스트)에서 **"지금 시스템이 정상인가"** 를 한 화면에
보여주기 위한 단일 엔드포인트다. 파이 터미널 모니터와 웹 대시보드 모니터링
페이지가 같은 응답을 소비한다.

설계 원칙 3가지:
1. **절대 500 을 내지 않는다.** 모니터가 죽으면 현장이 눈을 잃는다. 지표 수집·
   DB 조회는 모두 예외를 삼키고 해당 필드를 None/기본값으로 낮춘다.
2. **추가 의존성 없음.** psutil 같은 패키지를 파이에 넣지 않고 표준 라이브러리
   (/proc, /sys, os, shutil)만 읽는다. 파일이 없는 환경(맥/윈도우/컨테이너)에서는
   해당 지표가 None 이 된다.
3. **단일 산출원.** 처리속도 백분위는 리포트(core.report.proc_time_percentiles)와
   동일 함수를 재사용해 화면과 리포트 숫자가 어긋나지 않게 한다.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime, time as dtime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from aivis_types import LogCategory, Role

from core.config import get_settings
from core.heartbeat import last_seen as heartbeat_last_seen
from core.report import proc_time_percentiles
from core.security import CurrentUser, require_min_role
from db.base import get_db
from db.models import ActiveOrder, Inspection, SysLog

router = APIRouter(prefix="/system", tags=["system"])

# ---- 지표 소스 경로(파이 기준). 없으면 해당 지표는 None. --------------------
CPU_TEMP_PATH = "/sys/class/thermal/thermal_zone0/temp"  # 밀리도(m°C)
MEMINFO_PATH = "/proc/meminfo"
# 라즈베리파이 저전압/스로틀 비트마스크. vcgencmd 대신 sysfs 를 읽는다
# (subprocess 호출은 모니터 응답을 붙잡을 수 있어 쓰지 않는다).
THROTTLED_PATH = "/sys/devices/platform/soc/soc:firmware/get_throttled"

# ---- 워커 생존 판정 임계 ---------------------------------------------------
# 워커는 매 검사 사이클마다 POST /inspection/status 하트비트를 보낸다
# (기본 사이클 AIVIS_WORKER_INTERVAL_MS=1500ms → 초당 0.67회).
# - UP  15s: 기본 사이클의 10회분이자 워커의 기준정보 핫리로드 주기
#   (AIVIS_ITEM_RELOAD_S=15s)와 같다. 한두 사이클이 늦어도(네트워크 순간 지연,
#   무거운 프레임) 정상으로 본다.
# - STALE 60s: 40 사이클. 아직 죽었다고 단정하긴 이르지만 명백히 비정상 →
#   현장에 "확인 필요" 신호를 준다.
# - 60s 초과: down(카메라 취득 블로킹/프로세스 사망/네트워크 단절).
WORKER_UP_MAX_S = 15.0
WORKER_STALE_MAX_S = 60.0


# ---- 응답 스키마(라우터 내부 계약 — shared-types 미변경, BatchStatus 전례) --


class SystemMetrics(BaseModel):
    """호스트 자원 지표. 읽기 실패/미지원 환경이면 각 필드 None."""

    cpu_temp_c: Optional[float] = None  # CPU 온도(°C). 파이 외 환경 None
    cpu_percent: Optional[float] = None  # 1분 부하율 근사(%, 아래 주석 참조)
    load_1m: Optional[float] = None  # 1분 평균 부하
    mem_total_mb: Optional[float] = None
    mem_used_mb: Optional[float] = None
    mem_percent: Optional[float] = None
    disk_total_gb: Optional[float] = None
    disk_used_gb: Optional[float] = None
    disk_percent: Optional[float] = None
    throttled: Optional[bool] = None  # 파이 저전압/스로틀. 모르면 None


class ServicesStatus(BaseModel):
    """의존 서비스 상태. db=up|down, worker=up|stale|down."""

    db: str
    worker: str
    worker_last_seen_s: Optional[float] = None  # null = 기동 후 하트비트 없음


class WindowStats(BaseModel):
    """기간 집계. total=0 이면 ng_rate_pct=0.0(0 나눗셈 보호)."""

    total: int
    ng: int
    ng_rate_pct: float


class InspectionStats(BaseModel):
    """검사 라인 상태 요약."""

    last_hour: WindowStats
    today: WindowStats
    avg_proc_time_ms: Optional[float] = None  # 최근 1시간 표본. 없으면 None
    p95_proc_time_ms: Optional[float] = None  # 최근 1시간 표본. 없으면 None
    last_inspected_at: Optional[datetime] = None  # 검사 이력 없으면 None
    mes_pending: int  # mes_synced=false 누적 건수(연계 백로그)


class ActiveOrderInfo(BaseModel):
    """현재 검사 오더. 미설정 시 상위 필드가 통째로 null."""

    item_code: str
    lot: Optional[str] = None
    work_order: Optional[str] = None


class RecentError(BaseModel):
    """최근 오류 로그 1건."""

    ts: Optional[datetime] = None
    message: str


class SystemStatus(BaseModel):
    """GET /system/status 응답 전체."""

    ts: datetime
    system: SystemMetrics
    services: ServicesStatus
    inspection: InspectionStats
    active_order: Optional[ActiveOrderInfo] = None
    recent_errors: list[RecentError]


# ---- 시스템 지표 수집(표준 라이브러리만, 모든 읽기 try/except) --------------


def _read_text(path: str) -> str:
    """파일 텍스트 읽기. 실패 시 예외를 그대로 올린다(호출자가 None 처리)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _cpu_temp_c() -> Optional[float]:
    """CPU 온도(°C). sysfs 는 밀리도(m°C) 이므로 1000 으로 나눈다."""
    try:
        return round(int(_read_text(CPU_TEMP_PATH).strip()) / 1000.0, 1)
    except Exception:
        return None


def _load_1m() -> Optional[float]:
    """1분 평균 부하. os.getloadavg 미지원(윈도우) 시 None."""
    try:
        return round(float(os.getloadavg()[0]), 2)
    except Exception:
        return None


def _cpu_percent(load: Optional[float]) -> Optional[float]:
    """CPU 사용률 **근사값**(정확한 순간값 아님).

    psutil 없이 순간 CPU 사용률을 얻으려면 /proc/stat 을 두 번 읽는 사이에
    sleep 이 필요한데, 모니터 응답을 붙잡을 수 없으므로 쓰지 않는다. 대신
    `load_1m / CPU코어수 × 100` 으로 **최근 1분 부하율**을 백분율로 환산한다.
    (코어를 전부 쓰는 상태가 100%. 대기 프로세스가 쌓이면 100 을 넘을 수 있다.)
    """
    if load is None:
        return None
    try:
        cores = os.cpu_count()
        if not cores:
            return None
        return round(load / cores * 100.0, 1)
    except Exception:
        return None


def _memory() -> tuple[Optional[float], Optional[float], Optional[float]]:
    """(총 MB, 사용 MB, 사용률 %). /proc/meminfo 미지원 환경이면 (None,None,None).

    사용량 = MemTotal − MemAvailable (버퍼/캐시를 뺀 실사용 기준. free 명령의
    'available' 과 같은 정의로, 파이에서 실제 여유 메모리를 가장 잘 나타낸다).
    """
    try:
        values: dict[str, float] = {}
        for line in _read_text(MEMINFO_PATH).splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                values[key] = float(rest.strip().split()[0])  # kB
            if len(values) == 2:
                break
        total_kb = values["MemTotal"]
        avail_kb = values["MemAvailable"]
        if total_kb <= 0:
            return None, None, None
        used_kb = max(0.0, total_kb - avail_kb)
        return (
            round(total_kb / 1024.0, 1),
            round(used_kb / 1024.0, 1),
            round(used_kb / total_kb * 100.0, 1),
        )
    except Exception:
        return None, None, None


def _disk() -> tuple[Optional[float], Optional[float], Optional[float]]:
    """(총 GB, 사용 GB, 사용률 %). 검사 이미지가 쌓이는 볼륨 기준.

    이미지 디렉터리(AIVIS_IMAGES_DIR)가 존재하면 그 마운트를, 없으면 루트(/)를
    본다. 현장에서 디스크가 차면 이미지 저장이 먼저 실패하므로(§M7) 이 볼륨이
    감시 대상이다.
    """
    try:
        images_dir = get_settings().images_dir
        path = images_dir if images_dir and os.path.isdir(images_dir) else "/"
        usage = shutil.disk_usage(path)
        gb = 1024.0 ** 3
        percent = (usage.used / usage.total * 100.0) if usage.total else 0.0
        return (
            round(usage.total / gb, 1),
            round(usage.used / gb, 1),
            round(percent, 1),
        )
    except Exception:
        return None, None, None


def _throttled() -> Optional[bool]:
    """라즈베리파이 저전압/스로틀 여부. 판단 불가 환경이면 None.

    sysfs 값은 비트마스크(0x0=정상). 0 이 아니면 저전압·주파수 제한이 발생했거나
    발생한 적이 있다는 뜻으로 True 로 본다(현장 전원 어댑터 불량의 대표 증상).
    파일이 없으면(파이가 아니거나 커널 노출 안 됨) 억지로 vcgencmd 를 부르지 않고
    None 을 돌려준다.
    """
    try:
        raw = _read_text(THROTTLED_PATH).strip()
        return int(raw, 16 if raw.lower().startswith("0x") else 10) != 0
    except Exception:
        return None


def _collect_system() -> SystemMetrics:
    """호스트 지표 수집. 어떤 실패도 밖으로 던지지 않는다."""
    load = _load_1m()
    mem_total, mem_used, mem_percent = _memory()
    disk_total, disk_used, disk_percent = _disk()
    return SystemMetrics(
        cpu_temp_c=_cpu_temp_c(),
        cpu_percent=_cpu_percent(load),
        load_1m=load,
        mem_total_mb=mem_total,
        mem_used_mb=mem_used,
        mem_percent=mem_percent,
        disk_total_gb=disk_total,
        disk_used_gb=disk_used,
        disk_percent=disk_percent,
        throttled=_throttled(),
    )


# ---- 워커 생존 --------------------------------------------------------------


def _worker_state(now: datetime) -> tuple[str, Optional[float]]:
    """(worker 상태, 마지막 하트비트 경과 초). 하트비트 없으면 ("down", None)."""
    seen = heartbeat_last_seen()
    if seen is None:
        return "down", None
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    elapsed = max(0.0, (now - seen).total_seconds())
    if elapsed <= WORKER_UP_MAX_S:
        state = "up"
    elif elapsed <= WORKER_STALE_MAX_S:
        state = "stale"
    else:
        state = "down"
    return state, round(elapsed, 1)


# ---- 검사 통계 --------------------------------------------------------------


def _rate_pct(ng: int, total: int) -> float:
    """NG 비율(%). total 0 이면 0.0(0 나눗셈 보호)."""
    if total <= 0:
        return 0.0
    return round(ng / total * 100.0, 1)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """naive 값(sqlite 는 tz 를 저장하지 않는다)에 UTC 를 붙여 반환."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _window_stats(db: Session, since: datetime) -> WindowStats:
    """since 이후 (총 검사수, NG 수, NG율). 집계는 DB 에서 수행한다.

    전 테이블을 파이썬으로 끌어오면 100만 건 규모에서 모니터가 멈춘다. 기간
    필터(ix_insp_time)를 건 COUNT 두 개만 사용한다.
    """
    # COUNT(*) + SUM(CASE ...) 한 번의 스캔으로 총건수/NG건수를 함께 얻는다.
    # (FILTER 절 대신 CASE 를 쓰는 이유: 구버전 sqlite 를 포함해 모든 방언에서
    #  동일하게 동작한다 — 현장 파이의 개발용 sqlite 폴백까지 고려.)
    total, ng = db.execute(
        select(
            func.count(Inspection.id),
            func.sum(case((Inspection.final_verdict == "NG", 1), else_=0)),
        ).where(Inspection.inspected_at >= since)
    ).one()
    total = int(total or 0)
    ng = int(ng or 0)
    return WindowStats(total=total, ng=ng, ng_rate_pct=_rate_pct(ng, total))


def _inspection_stats(db: Session, now: datetime) -> InspectionStats:
    """검사 라인 요약(최근 1시간 / 오늘 0시(UTC) 이후 + 처리속도 + MES 백로그)."""
    hour_ago = now - timedelta(hours=1)
    today_start = datetime.combine(now.date(), dtime.min, tzinfo=timezone.utc)

    # 처리속도: 최근 1시간 표본만, proc_time_ms 컬럼만 select(행 전체 로드 금지).
    # 백분위는 리포트와 동일한 core.report.proc_time_percentiles 를 재사용한다
    # (같은 숫자가 화면/리포트에서 달라지지 않도록 단일 산출원 유지).
    proc_values = [
        int(v)
        for (v,) in db.execute(
            select(Inspection.proc_time_ms).where(
                Inspection.inspected_at >= hour_ago,
                Inspection.proc_time_ms.is_not(None),
            )
        ).all()
        if v is not None
    ]
    pct = proc_time_percentiles(
        [SimpleNamespace(proc_time_ms=v) for v in proc_values]
    )
    avg = round(sum(proc_values) / len(proc_values), 1) if proc_values else None

    # 마지막 검사 시각: ix_insp_time 인덱스로 1행만 읽는다.
    last_at = db.execute(
        select(Inspection.inspected_at)
        .order_by(Inspection.inspected_at.desc())
        .limit(1)
    ).scalar()

    # MES 미연계 백로그(누적) — 0 이 아니면 워치독/연계 상태를 봐야 한다(§7.3).
    mes_pending = db.execute(
        select(func.count(Inspection.id)).where(Inspection.mes_synced.is_(False))
    ).scalar()

    return InspectionStats(
        last_hour=_window_stats(db, hour_ago),
        today=_window_stats(db, today_start),
        avg_proc_time_ms=avg,
        p95_proc_time_ms=pct["p95"],
        last_inspected_at=_as_utc(last_at),
        mes_pending=int(mes_pending or 0),
    )


def _empty_inspection_stats() -> InspectionStats:
    """DB 조회 실패 시 폴백(엔드포인트는 그래도 200 을 반환한다)."""
    zero = WindowStats(total=0, ng=0, ng_rate_pct=0.0)
    return InspectionStats(
        last_hour=zero,
        today=zero,
        avg_proc_time_ms=None,
        p95_proc_time_ms=None,
        last_inspected_at=None,
        mes_pending=0,
    )


def _active_order(db: Session) -> Optional[ActiveOrderInfo]:
    """현재 검사 오더(단일 행 id=1). 미설정이면 None."""
    row = db.get(ActiveOrder, 1)
    if not row:
        return None
    return ActiveOrderInfo(
        item_code=row.item_code, lot=row.lot, work_order=row.work_order
    )


def _recent_errors(db: Session, limit: int = 5) -> list[RecentError]:
    """sys_log 최근 오류 5건(최신순). category=error 또는 level=ERROR.

    같은 초에 여러 건이 적재되면 ts 만으로는 순서가 갈리지 않으므로 id 를
    보조 정렬키로 쓴다(적재 순서 = id 순서).
    """
    rows = db.execute(
        select(SysLog.ts, SysLog.message)
        .where(
            or_(
                SysLog.category == LogCategory.ERROR.value,
                SysLog.level == "ERROR",
            )
        )
        .order_by(SysLog.ts.desc(), SysLog.id.desc())
        .limit(limit)
    ).all()
    return [RecentError(ts=_as_utc(ts), message=msg or "") for ts, msg in rows]


# ---- 엔드포인트 ------------------------------------------------------------


@router.get("/status", response_model=SystemStatus)
def system_status(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    """현장 시스템 상태 한 눈에 보기 (파이 터미널 모니터 + 대시보드 공용).

    반환 블록:
    - `system`: 호스트 자원(CPU 온도/부하/메모리/디스크/스로틀). 표준 라이브러리로만
      읽으며, 파이가 아니거나 파일이 없으면 해당 필드는 **null**.
      `cpu_percent` 는 순간값이 아니라 `load_1m ÷ 코어수 × 100` 근사값이다.
    - `services`: `db`(up|down), `worker`(up|stale|down),
      `worker_last_seen_s`(마지막 하트비트 경과 초, 기동 후 미수신이면 null).
      임계는 WORKER_UP_MAX_S=15s / WORKER_STALE_MAX_S=60s (워커 기본 사이클 1.5s,
      기준정보 핫리로드 15s 기준).
    - `inspection`: 최근 1시간/오늘(UTC 0시 이후) 검사·NG 수와 NG율(검사 0건이면 0.0),
      최근 1시간 처리속도 평균/p95(표본 없으면 null), 마지막 검사시각(null 가능),
      MES 미연계 누적 건수.
    - `active_order`: 현재 검사 오더. 미설정이면 **null**.
    - `recent_errors`: sys_log 오류 최근 5건(최신순). 없으면 빈 배열.

    이 엔드포인트는 어떤 이유로도 5xx 를 내지 않는다. DB 가 죽으면
    `services.db="down"` + 검사 통계 0/null 로 낮춰 응답한다(모니터가 함께
    죽으면 현장이 상태를 볼 수단을 잃는다).
    """
    now = datetime.now(timezone.utc)
    metrics = _collect_system()
    worker_state, worker_elapsed = _worker_state(now)

    db_state = "up"
    try:
        inspection = _inspection_stats(db, now)
    except Exception:
        db_state = "down"
        inspection = _empty_inspection_stats()

    try:
        active = _active_order(db) if db_state == "up" else None
    except Exception:
        db_state = "down"
        active = None

    try:
        errors = _recent_errors(db) if db_state == "up" else []
    except Exception:
        db_state = "down"
        errors = []

    return SystemStatus(
        ts=now,
        system=metrics,
        services=ServicesStatus(
            db=db_state,
            worker=worker_state,
            worker_last_seen_s=worker_elapsed,
        ),
        inspection=inspection,
        active_order=active,
        recent_errors=errors,
    )
