"""설비·가동 (docs/MES.md §11.4~11.5). 상태 전환은 라인에서 하므로 operator+."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from mes_app.common import today_kst
from mes_app.db import get_db
from mes_app.models import Equipment, EquipmentEvent, Process, WorkOrder
from mes_app.plant import open_event, set_state, utilization
from mes_app.schemas import (
    EquipmentEventOut, EquipmentIn, EquipmentOut, EquipmentPatch, EquipmentStateIn,
    UtilizationOut,
)
from mes_app.security import User, require_min_role

router = APIRouter(tags=["equipment"])
_read = require_min_role("operator")
_write = require_min_role("quality")


def _out(db: Session, eq: Equipment) -> EquipmentOut:
    ev = open_event(db, eq.code)
    wo = db.get(WorkOrder, ev.wo_id) if ev is not None and ev.wo_id else None
    return EquipmentOut(
        code=eq.code, name=eq.name, process_code=eq.process_code, note=eq.note, active=eq.active,
        state=ev.state if ev else None, state_since=ev.started_at if ev else None,
        state_reason=ev.reason if ev else None,
        wo_id=wo.id if wo else None, wo_no=wo.wo_no if wo else None,
    )


def _get(db: Session, code: str) -> Equipment:
    eq = db.get(Equipment, code)
    if not eq:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "설비 없음")
    return eq


def _check_process(db: Session, code: Optional[str]) -> None:
    if code and not db.get(Process, code):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"없는 공정: {code}")


@router.get("/equipment", response_model=list[EquipmentOut])
def list_equipment(db: Session = Depends(get_db), _u: User = Depends(_read)):
    return [_out(db, eq) for eq in db.execute(select(Equipment).order_by(Equipment.code)).scalars()]


@router.post("/equipment", response_model=EquipmentOut, status_code=status.HTTP_201_CREATED)
def create_equipment(body: EquipmentIn, db: Session = Depends(get_db),
                     _u: User = Depends(_write)):
    code = body.code.strip()
    if db.get(Equipment, code):
        raise HTTPException(status.HTTP_409_CONFLICT, f"설비 코드 중복: {code}")
    _check_process(db, body.process_code)
    eq = Equipment(code=code, name=body.name.strip(), process_code=body.process_code or None,
                   note=body.note)
    db.add(eq)
    db.commit()
    db.refresh(eq)
    return _out(db, eq)


@router.patch("/equipment/{code}", response_model=EquipmentOut)
def patch_equipment(code: str, body: EquipmentPatch, db: Session = Depends(get_db),
                    _u: User = Depends(_write)):
    eq = _get(db, code)
    data = body.model_dump(exclude_unset=True)
    _check_process(db, data.get("process_code"))
    for k, v in data.items():
        setattr(eq, k, v)
    db.commit()
    db.refresh(eq)
    return _out(db, eq)


@router.post("/equipment/{code}/state", response_model=EquipmentOut)
def change_state(code: str, body: EquipmentStateIn, db: Session = Depends(get_db),
                 user: User = Depends(_read)):
    eq = _get(db, code)
    reason = (body.reason or "").strip()
    if body.state in ("DOWN", "PM") and not reason:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "고장·점검은 사유를 적으세요")
    # 손으로 바꾸는 상태에는 작업지시를 붙이지 않는다. 다만 이미 작업지시로 가동 중인 설비를
    # 다시 '가동'으로 누르면 그대로 둔다(작업지시 연결이 끊기지 않게).
    cur = open_event(db, code)
    if cur is not None and cur.state == body.state == "RUN":
        return _out(db, eq)
    set_state(db, code, body.state, reason=reason or None, user=user.username)
    db.commit()
    return _out(db, eq)


@router.get("/equipment/{code}/events", response_model=list[EquipmentEventOut])
def list_events(code: str, limit: int = Query(100, ge=1, le=1000),
                db: Session = Depends(get_db), _u: User = Depends(_read)):
    _get(db, code)
    return list(db.execute(
        select(EquipmentEvent).where(EquipmentEvent.equipment_code == code)
        .order_by(EquipmentEvent.started_at.desc(), EquipmentEvent.id.desc()).limit(limit)
    ).scalars())


@router.get("/equipment-utilization", response_model=UtilizationOut)
def get_utilization(date_from: Optional[date] = None, date_to: Optional[date] = None,
                    db: Session = Depends(get_db), _u: User = Depends(_read)):
    d0 = date_from or today_kst()
    d1 = date_to or d0
    if d1 < d0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "끝 날짜가 시작보다 앞이다")
    return UtilizationOut(date_from=d0, date_to=d1, rows=utilization(db, d0, d1))
