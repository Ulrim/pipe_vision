"""출하·클레임·월 KPI (docs/MES.md §6.2). 조회 operator+, 등록·수정 quality+."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mes_app.common import next_no, today_kst
from mes_app.db import get_db
from mes_app.kpi import month_bounds, monthly
from mes_app.models import Claim, SalesOrder, Shipment
from mes_app.schemas import (
    ClaimIn,
    ClaimOut,
    ClaimPatch,
    MonthlyKpi,
    ShipmentIn,
    ShipmentOut,
)
from mes_app.security import User, require_min_role

router = APIRouter(tags=["logistics"])


def _ship_out(db: Session, s: Shipment) -> ShipmentOut:
    out = ShipmentOut.model_validate(s)
    if s.so_id:
        so = db.get(SalesOrder, s.so_id)
        if so:
            out.so_no = so.so_no
            out.lead_time_days = (s.ship_date - so.order_date).days
            out.on_time = (s.ship_date <= so.due_date) if so.due_date else None
    return out


@router.get("/shipments", response_model=list[ShipmentOut])
def list_shipments(
    period: Optional[str] = Query(None, description="YYYY-MM"),
    db: Session = Depends(get_db),
    _u: User = Depends(require_min_role("operator")),
):
    q = select(Shipment).order_by(Shipment.ship_date.desc(), Shipment.id.desc())
    if period:
        try:
            start, end = month_bounds(period)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
        q = q.where(Shipment.ship_date >= start, Shipment.ship_date < end)
    return [_ship_out(db, s) for s in db.execute(q.limit(500)).scalars()]


@router.post("/shipments", response_model=ShipmentOut, status_code=status.HTTP_201_CREATED)
def create_shipment(body: ShipmentIn, db: Session = Depends(get_db),
                    user: User = Depends(require_min_role("quality"))):
    item, customer = (body.item_code or "").strip(), (body.customer or "").strip()
    if body.so_id is not None:
        so = db.get(SalesOrder, body.so_id)
        if not so:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "수주 없음")
        item, customer = item or so.item_code, customer or so.customer
    if not item or not customer:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "품목·고객을 정하세요(또는 수주 선택)")
    s = Shipment(
        ship_no=(body.ship_no or "").strip() or next_no(db, Shipment.ship_no, "SH", on=body.ship_date),
        so_id=body.so_id, lot=(body.lot or None), item_code=item, customer=customer,
        qty=body.qty, ship_date=body.ship_date, note=body.note, created_by=user.username,
    )
    db.add(s)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"출하번호 중복: {s.ship_no}") from None
    db.refresh(s)
    return _ship_out(db, s)


@router.get("/claims", response_model=list[ClaimOut])
def list_claims(
    status_: Optional[str] = Query(None, alias="status"),
    db: Session = Depends(get_db),
    _u: User = Depends(require_min_role("operator")),
):
    q = select(Claim).order_by(Claim.occurred_date.desc(), Claim.id.desc())
    if status_:
        q = q.where(Claim.status == status_.upper())
    return list(db.execute(q.limit(500)).scalars())


@router.post("/claims", response_model=ClaimOut, status_code=status.HTTP_201_CREATED)
def create_claim(body: ClaimIn, db: Session = Depends(get_db),
                 user: User = Depends(require_min_role("quality"))):
    c = Claim(
        claim_no=(body.claim_no or "").strip() or next_no(db, Claim.claim_no, "CL", on=body.occurred_date),
        customer=body.customer.strip(), lot=body.lot or None, item_code=body.item_code or None,
        defect_qty=body.defect_qty,
        defect_type=(body.defect_type or "").strip().upper() or None,
        occurred_date=body.occurred_date, description=body.description,
        created_by=user.username,
    )
    db.add(c)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"클레임번호 중복: {c.claim_no}") from None
    db.refresh(c)
    return c


@router.patch("/claims/{claim_id}", response_model=ClaimOut)
def patch_claim(claim_id: int, body: ClaimPatch, db: Session = Depends(get_db),
                _u: User = Depends(require_min_role("quality"))):
    c = db.get(Claim, claim_id)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "클레임 없음")
    data = body.model_dump(exclude_unset=True)
    st = data.pop("status", None)
    if st and st != c.status:
        c.status = st
        c.closed_at = datetime.now(timezone.utc) if st == "CLOSED" else None
    if "defect_type" in data and data["defect_type"]:
        data["defect_type"] = data["defect_type"].strip().upper()
    for k, v in data.items():
        setattr(c, k, v)
    db.commit()
    db.refresh(c)
    return c


@router.get("/kpi/monthly", response_model=MonthlyKpi)
def kpi_monthly(
    period: Optional[str] = Query(None, description="YYYY-MM, 비우면 이번 달(KST)"),
    db: Session = Depends(get_db),
    _u: User = Depends(require_min_role("operator")),
):
    period = period or today_kst().strftime("%Y-%m")
    try:
        return MonthlyKpi(**monthly(db, period))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
