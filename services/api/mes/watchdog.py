"""MES 연계 워치독 + 연계 상태 조회 (CLAUDE.md §7.3, §5 M9).

연계율 100% 보장: inspection.mes_synced=false 인 행을 주기적으로 모아
어댑터로 재연계한다. table/rest 공통. 백그라운드 태스크(run_watchdog_forever)와
1회 실행(run_watchdog_once, CLI 용) 양쪽으로 기동 가능.

연계 상태(LinkageStatus)는 대시보드가 소비한다: 총건수/연계완료/미전송/실패.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aivis_types import LogCategory

from core.logging import write_log
from db.base import SessionLocal
from db.models import Inspection
from mes.adapter import MesAdapter, build_adapter
from mes.config import MesConfig, get_mes_config


@dataclass(frozen=True)
class WatchdogResult:
    """워치독 1회 실행 결과."""

    scanned: int   # 이번에 시도한 미전송 행 수
    synced: int    # 연계 성공 수
    failed: int    # 연계 실패 수(다음 주기 재시도 대상)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LinkageStatus:
    """MES 연계 상태 스냅샷(대시보드 모니터용, §7.3)."""

    total: int          # 전체 검사 건수
    synced: int         # 연계 완료(mes_synced=true)
    pending: int        # 미전송(mes_synced=false)
    rate: float         # 연계율(%) = synced/total*100
    mode: str           # 현재 MES 모드(table|rest)

    def as_dict(self) -> dict:
        return asdict(self)


def get_linkage_status(db: Session, cfg: MesConfig | None = None) -> LinkageStatus:
    """연계 상태 집계. total/synced/pending/rate."""
    cfg = cfg or get_mes_config()
    total = db.execute(select(func.count()).select_from(Inspection)).scalar_one()
    synced = db.execute(
        select(func.count())
        .select_from(Inspection)
        .where(Inspection.mes_synced.is_(True))
    ).scalar_one()
    pending = total - synced
    rate = (synced / total * 100.0) if total else 100.0
    return LinkageStatus(
        total=total,
        synced=synced,
        pending=pending,
        rate=round(rate, 4),
        mode=cfg.mode,
    )


def _pending_rows(db: Session, batch_size: int) -> list[Inspection]:
    """미전송(mes_synced=false) 행을 오래된 순으로 batch_size 만큼."""
    stmt = (
        select(Inspection)
        .where(Inspection.mes_synced.is_(False))
        .order_by(Inspection.inspected_at.asc())
        .limit(batch_size)
    )
    return list(db.execute(stmt).scalars().all())


def run_watchdog_once(
    db: Session,
    *,
    cfg: MesConfig | None = None,
    adapter: MesAdapter | None = None,
) -> WatchdogResult:
    """미전송 행 1배치를 재연계 시도. 연계율 100% 를 향한 1스텝."""
    cfg = cfg or get_mes_config()
    adapter = adapter or build_adapter(cfg)

    rows = _pending_rows(db, cfg.watchdog_batch_size)
    synced = 0
    failed = 0
    for row in rows:
        ok = adapter.sync_row(db, row)
        if ok:
            synced += 1
        else:
            failed += 1

    if rows:
        write_log(
            db,
            category=LogCategory.MES,
            message=(
                f"watchdog: scanned={len(rows)} synced={synced} failed={failed} "
                f"mode={cfg.mode}"
            ),
            payload={"scanned": len(rows), "synced": synced, "failed": failed,
                     "mode": cfg.mode},
        )
    return WatchdogResult(scanned=len(rows), synced=synced, failed=failed)


def run_watchdog_forever(
    *,
    cfg: MesConfig | None = None,
    stop_event=None,
    max_cycles: int | None = None,
    sleep_fn=time.sleep,
) -> None:
    """주기적 워치독 루프. 백그라운드 스레드/프로세스에서 기동.

    - cfg.watchdog_interval_s 마다 run_watchdog_once.
    - stop_event(threading.Event 류)가 set 되면 종료.
    - max_cycles 지정 시 그 횟수만 돌고 종료(테스트/배치 용).
    각 주기마다 새 세션을 열고 닫아 장기 실행 누수를 막는다.
    """
    cfg = cfg or get_mes_config()
    cycles = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        db = SessionLocal()
        try:
            run_watchdog_once(db, cfg=cfg)
        except Exception as exc:  # 루프는 죽지 않는다(연계 지속).
            try:
                write_log(
                    db,
                    category=LogCategory.MES,
                    level="ERROR",
                    message=f"watchdog 주기 오류: {exc}",
                )
            except Exception:
                pass
        finally:
            db.close()

        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break
        if stop_event is not None and stop_event.is_set():
            break
        sleep_fn(cfg.watchdog_interval_s)
