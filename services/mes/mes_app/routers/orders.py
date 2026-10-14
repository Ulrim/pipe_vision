"""수주·작업지시 (docs/MES.md §6.1). 조회 operator+, 등록·수정 quality+.

작업지시 상태(시작·완료)는 라인에서 바꾸므로 operator 도 할 수 있다. 수량·계획일은
quality+ 만 — AIVIS 오더 교체(PUT /master/items/{code}/spec)와 같은 원칙으로 범위를 좁힌다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mes_app.common import next_no
from mes_app.db import get_db
from mes_app.models import SalesOrder, Shipment, WorkOrder
from mes_app.production import progress_pct, records_for, summarize
from mes_app.schemas import (
    ProductionOut,
    SalesOrderIn,
    SalesOrderOut,
    SalesOrderPatch,
    WorkOrderDetail,
    WorkOrderIn,
    WorkOrderOut,
    WorkOrderPatch,
)
from mes_app.security import User, require_min_role

router = APIRouter(tags=["orders"])


# ---- 수주 ------------------------------------------------------------------------

def _so_out(db: Session, so: SalesOrder) -> SalesOrderOut:
    shipped = db.execute(
        select(func.coalesce(func.sum(Shipment.qty), 0)).where(Shipment.so_id == so.id)
    ).scalar() or 0
    wos = db.execute(
        select(func.count()).select_from(WorkOrder).where(WorkOrder.so_id == so.id)
    ).scalar() or 0
    out = SalesOrderOut.model_validate(so)
    out.shipped_qty = int(shipped)
    out.work_order_count = int(wos)
    return out


@router.get("/orders", response_model=list[SalesOrderOut])
def list_orders(
    status_: Optional[str] = Query(None, alias="status"),
    db: Session = Depends(get_db),
    _u: User = Depends(require_min_role("operator")),
):
    q = select(SalesOrder).order_by(SalesOrder.order_date.desc(), SalesOrder.id.desc())
    if status_:
        q = q.where(SalesOrder.status == status_.upper())
    return [_so_out(db, so) for so in db.execute(q.limit(500)).scalars()]


@router.post("/orders", response_model=SalesOrderOut, status_code=status.HTTP_201_CREATED)
def create_order(body: SalesOrderIn, db: Session = Depends(get_db),
                 user: User = Depends(require_min_role("quality"))):
    so = SalesOrder(
        so_no=(body.so_no or "").strip() or next_no(db, SalesOrder.so_no, "SO", on=body.order_date),
        customer=body.customer.strip(), item_code=body.item_code.strip(), qty=body.qty,
        order_date=body.order_date, due_date=body.due_date, note=body.note,
        created_by=user.username,
    )
    db.add(so)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"수주번호 중복: {so.so_no}") from None
    db.refresh(so)
    return _so_out(db, so)


@router.patch("/orders/{so_id}", response_model=SalesOrderOut)
def patch_order(so_id: int, body: SalesOrderPatch, db: Session = Depends(get_db),
                _u: User = Depends(require_min_role("quality"))):
    so = db.get(SalesOrder, so_id)
    if not so:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "수주 없음")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(so, k, v)
    db.commit()
    db.refresh(so)
    return _so_out(db, so)


# ---- 작업지시 ----------------------------------------------------------------------

def _wo_out(db: Session, wo: WorkOrder, prod=None) -> WorkOrderOut:
    if prod is None:
        prod = summarize(records_for(db, wo))
    out = WorkOrderOut.model_validate(wo)
    if wo.so_id:
        so = db.get(SalesOrder, wo.so_id)
        if so:
            out.so_no, out.customer = so.so_no, so.customer
    out.good_qty = prod.good_qty
    out.defect_total = prod.defect_total
    out.inspected_rows = prod.inspected_rows
    out.progress_pct = progress_pct(prod.good_qty, wo.plan_qty)
    out.last_at = prod.last_at
    return out


@router.get("/work-orders", response_model=list[WorkOrderOut])
def list_work_orders(
    status_: Optional[str] = Query(None, alias="status", description="쉼표로 여러 개"),
    db: Session = Depends(get_db),
    _u: User = Depends(require_min_role("operator")),
):
    q = select(WorkOrder).order_by(WorkOrder.id.desc())
    if status_:
        q = q.where(WorkOrder.status.in_([s.strip().upper() for s in status_.split(",")]))
    return [_wo_out(db, wo) for wo in db.execute(q.limit(300)).scalars()]


@router.post("/work-orders", response_model=WorkOrderOut, status_code=status.HTTP_201_CREATED)
def create_work_order(body: WorkOrderIn, db: Session = Depends(get_db),
                      user: User = Depends(require_min_role("quality"))):
    item = (body.item_code or "").strip()
    if body.so_id is not None:
        so = db.get(SalesOrder, body.so_id)
        if not so:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "수주 없음")
        item = item or so.item_code
    if not item:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "품목을 정하세요")
    on = body.plan_date
    wo = WorkOrder(
        wo_no=(body.wo_no or "").strip() or next_no(db, WorkOrder.wo_no, "WO", on=on),
        so_id=body.so_id, item_code=item,
        lot=(body.lot or "").strip() or next_no(db, WorkOrder.lot, "L", width=2, on=on),
        plan_qty=body.plan_qty, plan_date=body.plan_date, note=body.note,
        created_by=user.username,
    )
    db.add(wo)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"작업지시번호 또는 LOT 중복: {wo.wo_no} / {wo.lot}"
        ) from None
    db.refresh(wo)
    # 검사결과가 먼저 들어와 있던 LOT(작업지시를 늦게 만든 경우)은 바로 진행 중으로 —
    # 시작 시각은 첫 검사.
    prod = summarize(records_for(db, wo))
    if prod.first_at is not None:
        wo.status, wo.started_at = "RUNNING", prod.first_at
        db.commit()
        db.refresh(wo)
    return _wo_out(db, wo, prod)


@router.get("/work-orders/{wo_id}", response_model=WorkOrderDetail)
def get_work_order(wo_id: int, db: Session = Depends(get_db),
                   _u: User = Depends(require_min_role("operator"))):
    wo = db.get(WorkOrder, wo_id)
    if not wo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "작업지시 없음")
    prod = summarize(records_for(db, wo))
    return WorkOrderDetail(work_order=_wo_out(db, wo, prod),
                           production=ProductionOut(**prod.as_dict()))


@router.patch("/work-orders/{wo_id}", response_model=WorkOrderOut)
def patch_work_order(wo_id: int, body: WorkOrderPatch, db: Session = Depends(get_db),
                     user: User = Depends(require_min_role("operator"))):
    wo = db.get(WorkOrder, wo_id)
    if not wo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "작업지시 없음")
    data = body.model_dump(exclude_unset=True)
    if set(data) - {"status"} and user.role == "operator":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "수량·계획일은 품질관리자 이상만 바꾼다")
    now = datetime.now(timezone.utc)
    new_status = data.pop("status", None)
    if new_status and new_status != wo.status:
        if new_status == "RUNNING" and wo.started_at is None:
            wo.started_at = now
        if new_status in ("DONE", "CANCELLED"):
            wo.finished_at = now
        if new_status in ("PLANNED", "RUNNING"):
            wo.finished_at = None
        wo.status = new_status
    for k, v in data.items():
        setattr(wo, k, v)
    db.commit()
    db.refresh(wo)
    return _wo_out(db, wo)
