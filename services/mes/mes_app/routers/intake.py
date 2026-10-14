"""수신·연계 현황·생산실적 조회 (docs/MES.md §6.3)."""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from mes_app.common import KST, today_kst
from mes_app.config import get_settings
from mes_app.db import get_db
from mes_app.intake import autostart_work_orders, record_rest_run, upsert_record
from mes_app.models import IntakeRun, QualityRecord
from mes_app.production import unassigned_lots
from mes_app.schemas import (
    IntakeRunOut,
    IntakeStatus,
    PollOut,
    QualityIn,
    QualityOut,
    UnassignedLot,
)
from mes_app.security import User, require_intake_token, require_min_role

router = APIRouter(tags=["intake"])


def _poller(request: Request):
    return getattr(request.app.state, "poller", None)


@router.post("/intake/quality", dependencies=[Depends(require_intake_token)])
def intake_quality(
    body: QualityIn,
    db: Session = Depends(get_db),
    idem_header: Optional[str] = Header(None, alias="X-Idempotency-Key"),
):
    """AIVIS → MES REST 수신(MES_INTERFACE §3). 같은 키는 갱신하고 200 — 에러로 답하면
    AIVIS 가 계속 재시도한다."""
    data = body.model_dump()
    data["idem_key"] = body.idem_key or idem_header
    if not data["idem_key"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "idem_key(본문 또는 헤더) 필요")
    try:
        result = upsert_record(db, data, source="rest")
        autostart_work_orders(db, [data])
        if result != "duplicate":
            record_rest_run(db, result)
        db.commit()
    except (SQLAlchemyError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"적재 실패: {exc}") from None
    return {"status": result, "idem_key": data["idem_key"]}


@router.post("/intake/poll", response_model=PollOut)
def intake_poll(request: Request, _u: User = Depends(require_min_role("quality"))):
    """지금 가져오기(연계 현황 화면). 주기 폴링과 겹치지 않게 같은 잠금을 쓴다."""
    poller = _poller(request)
    if poller is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "테이블 연계가 꺼져 있다(MES_AIVIS_DB_URL 미설정 — REST 수신만 받는다)")
    return PollOut(**poller.run_once().as_dict())


@router.get("/intake/status", response_model=IntakeStatus)
def intake_status(request: Request, db: Session = Depends(get_db),
                  _u: User = Depends(require_min_role("operator"))):
    s = get_settings()
    poller = _poller(request)
    pending, iface_err = None, None
    if poller is not None:
        try:
            pending = poller.reader.pending_count()
        except SQLAlchemyError as exc:
            poller.reader.reset()
            iface_err = f"{exc.__class__.__name__}: {exc}"
    start = datetime.combine(today_kst(), time(0), tzinfo=KST).astimezone(timezone.utc)
    total = db.execute(select(func.count()).select_from(QualityRecord)).scalar() or 0
    today = db.execute(
        select(func.count()).select_from(QualityRecord).where(QualityRecord.received_at >= start)
    ).scalar() or 0
    last = db.execute(select(func.max(QualityRecord.received_at))).scalar()
    runs = db.execute(select(IntakeRun).order_by(IntakeRun.id.desc()).limit(20)).scalars()
    lr = poller.last_result if poller else None
    return IntakeStatus(
        table_enabled=s.table_intake_enabled,
        poller_running=bool(poller and poller._thread is not None),
        poll_interval_s=s.poll_interval_s,
        last_poll_at=poller.last_poll_at if poller else None,
        last_result=PollOut(**lr.as_dict()) if lr else None,
        interface_pending=pending,
        interface_error=iface_err,
        rest_token_required=bool(s.intake_token),
        records_total=int(total),
        records_today=int(today),
        last_received_at=last,
        runs=[IntakeRunOut.model_validate(r) for r in runs],
    )


@router.get("/quality", response_model=list[QualityOut])
def list_quality(
    lot: Optional[str] = None,
    work_order: Optional[str] = None,
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
    _u: User = Depends(require_min_role("operator")),
):
    """받은 품질이력(추적용). 최근 것부터."""
    q = select(QualityRecord).order_by(QualityRecord.inspected_at.desc(), QualityRecord.id.desc())
    if lot:
        q = q.where(QualityRecord.lot == lot)
    if work_order:
        q = q.where(QualityRecord.work_order == work_order)
    return list(db.execute(q.limit(limit)).scalars())


@router.get("/production/unassigned", response_model=list[UnassignedLot])
def production_unassigned(db: Session = Depends(get_db),
                          _u: User = Depends(require_min_role("operator"))):
    """작업지시와 이어지지 않은 LOT 의 실적 — 작업지시를 늦게 만들었거나 LOT 을 잘못 넣은 경우."""
    return unassigned_lots(db)
