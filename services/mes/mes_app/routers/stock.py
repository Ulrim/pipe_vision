"""자재·재고 (docs/MES.md §11). 현재고는 수불의 합 — 저장하지 않는다."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mes_app.db import get_db
from mes_app.models import Item, StockTxn, WorkOrder
from mes_app.plant import now_utc
from mes_app.schemas import AdjustIn, ReceiptIn, StockRow, StockTxnOut
from mes_app.security import User, require_min_role

router = APIRouter(tags=["stock"])
_read = require_min_role("operator")


def txn_out(db: Session, t: StockTxn) -> StockTxnOut:
    out = StockTxnOut.model_validate(t)
    if t.wo_id:
        wo = db.get(WorkOrder, t.wo_id)
        out.wo_no = wo.wo_no if wo else None
    return out


@router.get("/stock", response_model=list[StockRow])
def list_stock(item_type: Optional[str] = None, db: Session = Depends(get_db),
               _u: User = Depends(_read)):
    """품목별 현재고. 기준정보에 있는 품목 전부 + 기준정보에 없어도 수불이 있는 품목."""
    sums = {code: (float(q or 0), last) for code, q, last in db.execute(
        select(StockTxn.item_code, func.sum(StockTxn.qty), func.max(StockTxn.ts))
        .group_by(StockTxn.item_code)
    ).all()}
    items = {it.item_code: it for it in db.execute(select(Item)).scalars()}
    want = item_type.upper() if item_type else None
    rows = []
    for code in sorted(set(items) | set(sums)):
        it = items.get(code)
        if it is not None and not it.active and code not in sums:
            continue
        if want and (it is None or it.item_type != want):
            continue
        qty, last = sums.get(code, (0.0, None))
        safety = float(it.safety_stock) if it else 0.0
        rows.append(StockRow(
            item_code=code, name=it.name if it else None,
            item_type=it.item_type if it else None, unit=it.unit if it else None,
            on_hand=round(qty, 6), safety_stock=safety,
            below_safety=safety > 0 and qty < safety, last_txn_at=last,
        ))
    return rows


@router.get("/stock/txns", response_model=list[StockTxnOut])
def list_txns(item_code: Optional[str] = None, wo_id: Optional[int] = None,
              limit: int = Query(200, ge=1, le=2000),
              db: Session = Depends(get_db), _u: User = Depends(_read)):
    q = select(StockTxn).order_by(StockTxn.ts.desc(), StockTxn.id.desc())
    if item_code:
        q = q.where(StockTxn.item_code == item_code)
    if wo_id is not None:
        q = q.where(StockTxn.wo_id == wo_id)
    return [txn_out(db, t) for t in db.execute(q.limit(limit)).scalars()]


def _item(db: Session, code: str) -> Item:
    it = db.get(Item, code.strip())
    if not it:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"품목 기준정보에 없는 품목: {code} — 먼저 품목을 등록하세요")
    return it


@router.post("/stock/receipts", response_model=StockTxnOut, status_code=status.HTTP_201_CREATED)
def receive(body: ReceiptIn, db: Session = Depends(get_db), user: User = Depends(_read)):
    it = _item(db, body.item_code)
    t = StockTxn(ts=now_utc(), item_code=it.item_code, lot=(body.lot or "").strip() or None,
                 qty=body.qty, txn_type="RECEIPT", note=body.note, created_by=user.username)
    db.add(t)
    db.commit()
    db.refresh(t)
    return txn_out(db, t)


@router.post("/stock/adjustments", response_model=StockTxnOut,
             status_code=status.HTTP_201_CREATED)
def adjust(body: AdjustIn, db: Session = Depends(get_db),
           user: User = Depends(require_min_role("quality"))):
    if body.qty == 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "조정 수량이 0 이다")
    code = body.item_code.strip()
    # 제품은 기준정보에 없어도 생산·출하로 수불이 생긴다 — 그런 품목도 조정할 수 있어야 한다.
    known = db.get(Item, code) or db.execute(
        select(StockTxn.id).where(StockTxn.item_code == code).limit(1)).scalar()
    if not known:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"품목도 수불도 없는 코드: {code}")
    t = StockTxn(ts=now_utc(), item_code=code, lot=(body.lot or "").strip() or None,
                 qty=body.qty, txn_type="ADJUST", note=body.note.strip(),
                 created_by=user.username)
    db.add(t)
    db.commit()
    db.refresh(t)
    return txn_out(db, t)
