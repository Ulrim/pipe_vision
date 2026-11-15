"""기준정보: 품목·BOM·공정·라우팅 (docs/MES.md §11.5). 조회 operator+, 등록·수정 quality+."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mes_app.db import get_db
from mes_app.models import Bom, Equipment, Item, Process, Routing
from mes_app.plant import DEFAULT_ITEM, resolve_routing
from mes_app.schemas import (
    BomIn, BomLineOut, ItemIn, ItemOut, ItemPatch, ProcessIn, ProcessOut, ProcessPatch,
    RoutingIn, RoutingOut,
)
from mes_app.security import User, require_min_role

router = APIRouter(tags=["master"])
_read = require_min_role("operator")
_write = require_min_role("quality")


# ---- 품목 --------------------------------------------------------------------------

@router.get("/items", response_model=list[ItemOut])
def list_items(item_type: Optional[str] = None, active: Optional[bool] = None,
               db: Session = Depends(get_db), _u: User = Depends(_read)):
    q = select(Item).order_by(Item.item_type, Item.item_code)
    if item_type:
        q = q.where(Item.item_type == item_type.upper())
    if active is not None:
        q = q.where(Item.active.is_(active))
    return list(db.execute(q).scalars())


@router.post("/items", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
def create_item(body: ItemIn, db: Session = Depends(get_db), _u: User = Depends(_write)):
    code = body.item_code.strip()
    if db.get(Item, code):
        raise HTTPException(status.HTTP_409_CONFLICT, f"품목 코드 중복: {code}")
    it = Item(item_code=code, name=body.name.strip(), item_type=body.item_type,
              unit=body.unit.strip(), safety_stock=body.safety_stock, spec=body.spec)
    db.add(it)
    db.commit()
    db.refresh(it)
    return it


@router.patch("/items/{item_code}", response_model=ItemOut)
def patch_item(item_code: str, body: ItemPatch, db: Session = Depends(get_db),
               _u: User = Depends(_write)):
    it = db.get(Item, item_code)
    if not it:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "품목 없음")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(it, k, v)
    db.commit()
    db.refresh(it)
    return it


# ---- BOM ---------------------------------------------------------------------------

def _bom_out(db: Session, rows) -> list[BomLineOut]:
    out = []
    for b in rows:
        comp = db.get(Item, b.component_code)
        out.append(BomLineOut(
            parent_code=b.parent_code, component_code=b.component_code,
            component_name=comp.name if comp else None, unit=comp.unit if comp else None,
            qty_per=b.qty_per, scrap_pct=b.scrap_pct, note=b.note))
    return out


@router.get("/bom", response_model=list[BomLineOut])
def list_bom(parent: Optional[str] = Query(None, description="비우면 전체"),
             db: Session = Depends(get_db), _u: User = Depends(_read)):
    q = select(Bom).order_by(Bom.parent_code, Bom.component_code)
    if parent:
        q = q.where(Bom.parent_code == parent)
    return _bom_out(db, db.execute(q).scalars())


@router.put("/bom/{parent_code}", response_model=list[BomLineOut])
def put_bom(parent_code: str, body: BomIn, db: Session = Depends(get_db),
            _u: User = Depends(_write)):
    """그 제품의 BOM 을 통째로 바꾼다(빈 목록이면 지운다)."""
    seen: set[str] = set()
    for ln in body.lines:
        code = ln.component_code.strip()
        if code == parent_code:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "제품 자신을 자재로 넣을 수 없다")
        if code in seen:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"자재 중복: {code}")
        if not db.get(Item, code):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"품목 기준정보에 없는 자재: {code}")
        seen.add(code)
    db.execute(delete(Bom).where(Bom.parent_code == parent_code))
    for ln in body.lines:
        db.add(Bom(parent_code=parent_code, component_code=ln.component_code.strip(),
                   qty_per=ln.qty_per, scrap_pct=ln.scrap_pct, note=ln.note))
    db.commit()
    return _bom_out(db, db.execute(
        select(Bom).where(Bom.parent_code == parent_code).order_by(Bom.component_code)
    ).scalars())


# ---- 공정 --------------------------------------------------------------------------

@router.get("/processes", response_model=list[ProcessOut])
def list_processes(db: Session = Depends(get_db), _u: User = Depends(_read)):
    return list(db.execute(select(Process).order_by(Process.seq, Process.code)).scalars())


@router.post("/processes", response_model=ProcessOut, status_code=status.HTTP_201_CREATED)
def create_process(body: ProcessIn, db: Session = Depends(get_db), _u: User = Depends(_write)):
    code = body.code.strip().upper()
    if db.get(Process, code):
        raise HTTPException(status.HTTP_409_CONFLICT, f"공정 코드 중복: {code}")
    p = Process(code=code, name=body.name.strip(), seq=body.seq,
                auto_from_inspection=body.auto_from_inspection)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.patch("/processes/{code}", response_model=ProcessOut)
def patch_process(code: str, body: ProcessPatch, db: Session = Depends(get_db),
                  _u: User = Depends(_write)):
    p = db.get(Process, code)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "공정 없음")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return p


# ---- 라우팅 ------------------------------------------------------------------------

@router.get("/routings/{item_code}", response_model=RoutingOut)
def get_routing(item_code: str, db: Session = Depends(get_db), _u: User = Depends(_read)):
    source, steps = resolve_routing(db, item_code)
    return RoutingOut(item_code=item_code, source=source, steps=steps)


@router.put("/routings/{item_code}", response_model=RoutingOut)
def put_routing(item_code: str, body: RoutingIn, db: Session = Depends(get_db),
                _u: User = Depends(_write)):
    """그 품목(또는 공통 '*')의 라우팅을 통째로 바꾼다. 빈 목록이면 전용 라우팅을 지운다."""
    for st in body.steps:
        if not db.get(Process, st.process_code):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"없는 공정: {st.process_code}")
        if st.equipment_code and not db.get(Equipment, st.equipment_code):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"없는 설비: {st.equipment_code}")
    db.execute(delete(Routing).where(Routing.item_code == item_code))
    for i, st in enumerate(body.steps):
        db.add(Routing(item_code=item_code, seq=(i + 1) * 10, process_code=st.process_code,
                       equipment_code=st.equipment_code or None,
                       std_sec_per_ea=st.std_sec_per_ea))
    try:
        db.commit()
    except IntegrityError:  # pragma: no cover - 동시에 같은 품목을 저장
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "다른 사용자가 같은 라우팅을 저장했습니다") from None
    source, steps = resolve_routing(db, item_code)
    return RoutingOut(item_code=item_code, source=source, steps=steps)


__all__ = ["router", "DEFAULT_ITEM"]
