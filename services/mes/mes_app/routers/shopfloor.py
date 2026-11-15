"""공정 진행·자재 투입 (docs/MES.md §11.3, §11.5). 라인에서 하는 일이라 operator+."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from mes_app.db import get_db
from mes_app.models import Equipment, Item, Process, StockTxn, WoOperation, WorkOrder
from mes_app.plant import (
    ensure_operations, material_lines, now_utc, on_hand, open_event, set_state,
)
from mes_app.production import records_for, summarize
from mes_app.schemas import (
    MaterialIssueIn, MaterialsOut, OpCompleteIn, OperationOut, OpStartIn,
)
from mes_app.security import User, require_min_role

router = APIRouter(tags=["shopfloor"])
_op = require_min_role("operator")


def _wo(db: Session, wo_id: int) -> WorkOrder:
    wo = db.get(WorkOrder, wo_id)
    if not wo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "작업지시 없음")
    return wo


def _operation(db: Session, wo: WorkOrder, op_id: int) -> WoOperation:
    ensure_operations(db, wo)
    op = db.get(WoOperation, op_id)
    if not op or op.wo_id != wo.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "공정 없음")
    return op


def _is_auto(db: Session, op: WoOperation) -> bool:
    p = db.get(Process, op.process_code)
    return bool(p and p.auto_from_inspection)


def _ops_out(db: Session, wo: WorkOrder) -> list[OperationOut]:
    ops = ensure_operations(db, wo)
    prod = None
    out = []
    for op in ops:
        o = OperationOut.model_validate(op)
        if _is_auto(db, op):
            if prod is None:
                prod = summarize(records_for(db, wo))
            o.auto = True
            if prod.inspected_rows:
                o.auto_good_qty, o.auto_defect_qty = int(prod.good_qty), int(prod.defect_total)
        out.append(o)
    return out


def _closed(wo: WorkOrder) -> None:
    if wo.status in ("DONE", "CANCELLED"):
        raise HTTPException(status.HTTP_409_CONFLICT, "완료·취소된 작업지시다 — 먼저 되돌리세요")


@router.get("/work-orders/{wo_id}/operations", response_model=list[OperationOut])
def list_operations(wo_id: int, db: Session = Depends(get_db), _u: User = Depends(_op)):
    return _ops_out(db, _wo(db, wo_id))


@router.post("/work-orders/{wo_id}/operations/{op_id}/start",
             response_model=list[OperationOut])
def start_operation(wo_id: int, op_id: int, body: OpStartIn, db: Session = Depends(get_db),
                    user: User = Depends(_op)):
    wo = _wo(db, wo_id)
    _closed(wo)
    op = _operation(db, wo, op_id)
    if op.status != "PENDING":
        raise HTTPException(status.HTTP_409_CONFLICT, f"이미 {op.status} 인 공정이다")
    code = (body.equipment_code or "").strip() or op.equipment_code
    if code:
        eq = db.get(Equipment, code)
        if not eq or not eq.active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"없거나 쓰지 않는 설비: {code}")
        cur = open_event(db, code)
        if cur is not None and cur.state in ("DOWN", "PM"):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"설비 {code} 가 {'고장' if cur.state == 'DOWN' else '점검'} 상태다")
        if cur is not None and cur.state == "RUN" and cur.wo_id not in (None, wo.id):
            raise HTTPException(status.HTTP_409_CONFLICT, f"설비 {code} 가 다른 작업지시로 가동 중이다")
        set_state(db, code, "RUN", wo_id=wo.id, user=user.username)
    now = now_utc()
    op.status, op.started_at, op.equipment_code = "RUNNING", now, code or None
    op.worker = (body.worker or "").strip() or user.username
    if wo.status == "PLANNED":
        wo.status = "RUNNING"
        wo.started_at = wo.started_at or now
    db.commit()
    return _ops_out(db, wo)


def _release_equipment(db: Session, wo: WorkOrder, op: WoOperation, user: User) -> None:
    """이 공정이 쓰던 설비가 이 작업지시로 가동 중이면 대기로."""
    if not op.equipment_code:
        return
    cur = open_event(db, op.equipment_code)
    if cur is not None and cur.state == "RUN" and cur.wo_id == wo.id:
        set_state(db, op.equipment_code, "IDLE", user=user.username)


@router.post("/work-orders/{wo_id}/operations/{op_id}/complete",
             response_model=list[OperationOut])
def complete_operation(wo_id: int, op_id: int, body: OpCompleteIn,
                       db: Session = Depends(get_db), user: User = Depends(_op)):
    wo = _wo(db, wo_id)
    _closed(wo)
    op = _operation(db, wo, op_id)
    if op.status not in ("PENDING", "RUNNING"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"이미 {op.status} 인 공정이다")
    if body.good_qty is None and not _is_auto(db, op):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "양품 수량을 넣으세요")
    now = now_utc()
    op.started_at = op.started_at or now
    op.status, op.ended_at = "DONE", now
    # 검사 공정에서 수량을 비우면 저장하지 않는다 — 읽을 때마다 검사결과에서 센다(재확인 반영).
    op.good_qty = body.good_qty
    op.defect_qty = body.defect_qty if body.good_qty is not None else None
    if body.note:
        op.note = body.note
    op.worker = op.worker or user.username
    _release_equipment(db, wo, op, user)
    db.commit()
    return _ops_out(db, wo)


@router.post("/work-orders/{wo_id}/operations/{op_id}/skip",
             response_model=list[OperationOut])
def skip_operation(wo_id: int, op_id: int, db: Session = Depends(get_db),
                   user: User = Depends(require_min_role("quality"))):
    wo = _wo(db, wo_id)
    _closed(wo)
    op = _operation(db, wo, op_id)
    if op.status not in ("PENDING", "RUNNING"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"이미 {op.status} 인 공정이다")
    op.status, op.ended_at = "SKIPPED", now_utc()
    _release_equipment(db, wo, op, user)
    db.commit()
    return _ops_out(db, wo)


# ---- 자재 소요·투입 ------------------------------------------------------------------

def _materials(db: Session, wo: WorkOrder) -> MaterialsOut:
    return MaterialsOut(wo_id=wo.id, plan_qty=wo.plan_qty, lines=material_lines(db, wo))


@router.get("/work-orders/{wo_id}/materials", response_model=MaterialsOut)
def get_materials(wo_id: int, db: Session = Depends(get_db), _u: User = Depends(_op)):
    return _materials(db, _wo(db, wo_id))


@router.post("/work-orders/{wo_id}/materials/issue", response_model=MaterialsOut)
def issue_materials(wo_id: int, body: MaterialIssueIn, db: Session = Depends(get_db),
                    user: User = Depends(_op)):
    wo = _wo(db, wo_id)
    _closed(wo)
    if body.lines is None:
        lines = [(ln["item_code"], round(ln["required"] - ln["issued"], 6), None)
                 for ln in material_lines(db, wo) if ln["required"] - ln["issued"] > 1e-9]
        if not lines:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "투입할 자재가 없다(BOM 이 없거나 이미 다 투입했다)")
    else:
        lines = [(ln.item_code.strip(), ln.qty, (ln.lot or "").strip() or None)
                 for ln in body.lines]
        for code, _q, _l in lines:
            if not db.get(Item, code):
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"품목 기준정보에 없는 자재: {code}")
    need: dict[str, float] = {}
    for code, qty, _l in lines:
        need[code] = need.get(code, 0.0) + qty
    short = {c: round(q - on_hand(db, c), 6) for c, q in need.items() if q - on_hand(db, c) > 1e-9}
    if short and not body.allow_negative:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "message": "재고 부족: " + ", ".join(f"{c} {q:g}" for c, q in sorted(short.items())),
            "shortage": short,
        })
    now = now_utc()
    for code, qty, lot in lines:
        db.add(StockTxn(ts=now, item_code=code, lot=lot, qty=-qty, txn_type="ISSUE",
                        wo_id=wo.id, note=f"작업지시 {wo.wo_no} 투입", created_by=user.username))
    db.commit()
    return _materials(db, wo)
