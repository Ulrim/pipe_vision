"""기준정보·공정·재고·설비의 공통 로직 (docs/MES.md §11). 라우터는 이것을 부르기만 한다."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mes_app.common import KST
from mes_app.models import (
    Bom, Equipment, EquipmentEvent, Item, Process, Routing, StockTxn, WoOperation, WorkOrder,
)
from mes_app.production import records_for, summarize

DEFAULT_ITEM = "*"
#: 처음 기동 때 넣는 공정(도입기업 공정: 절단 → 디버링 → 세척 → 검사 → 포장).
DEFAULT_PROCESSES = (
    ("CUT", "절단", 10, False),
    ("DEBURR", "디버링", 20, False),
    ("WASH", "세척", 30, False),
    ("INSPECT", "검사", 40, True),
    ("PACK", "포장", 50, False),
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def seed_processes(db: Session) -> None:
    """공정 표가 비어 있을 때만 기본 공정을 넣는다(지운 공정을 되살리지 않는다)."""
    if db.execute(select(func.count()).select_from(Process)).scalar():
        return
    for code, name, seq, auto in DEFAULT_PROCESSES:
        db.add(Process(code=code, name=name, seq=seq, auto_from_inspection=auto))
    db.commit()


# ---- 라우팅 ------------------------------------------------------------------------

def resolve_routing(db: Session, item_code: str) -> tuple[str, list[dict]]:
    """그 품목에 적용되는 라우팅: 전용 → 공통('*') → 사용 중인 공정 전부. (source, steps)."""
    names = {p.code: p.name for p in db.execute(select(Process)).scalars()}
    tries = [(DEFAULT_ITEM, "default")]
    if item_code != DEFAULT_ITEM:
        tries.insert(0, (item_code, "item"))
    for code, source in tries:
        rows = list(db.execute(
            select(Routing).where(Routing.item_code == code).order_by(Routing.seq)
        ).scalars())
        if rows:
            return source, [
                {"seq": r.seq, "process_code": r.process_code,
                 "process_name": names.get(r.process_code, r.process_code),
                 "equipment_code": r.equipment_code, "std_sec_per_ea": r.std_sec_per_ea}
                for r in rows
            ]
    procs = db.execute(
        select(Process).where(Process.active.is_(True)).order_by(Process.seq, Process.code)
    ).scalars()
    return "processes", [
        {"seq": (i + 1) * 10, "process_code": p.code, "process_name": p.name,
         "equipment_code": None, "std_sec_per_ea": None}
        for i, p in enumerate(procs)
    ]


def ensure_operations(db: Session, wo: WorkOrder) -> list[WoOperation]:
    """작업지시의 공정 목록. 없으면 지금 라우팅에서 만든다(만든 뒤에는 라우팅을 바꿔도 그대로)."""
    q = select(WoOperation).where(WoOperation.wo_id == wo.id).order_by(WoOperation.seq)
    ops = list(db.execute(q).scalars())
    if ops:
        return ops
    _source, steps = resolve_routing(db, wo.item_code)
    for st in steps:
        db.add(WoOperation(wo_id=wo.id, seq=st["seq"], process_code=st["process_code"],
                           process_name=st["process_name"], equipment_code=st["equipment_code"]))
    db.commit()
    return list(db.execute(q).scalars())


# ---- 재고 --------------------------------------------------------------------------

def on_hand(db: Session, item_code: str) -> float:
    return float(db.execute(
        select(func.coalesce(func.sum(StockTxn.qty), 0)).where(StockTxn.item_code == item_code)
    ).scalar() or 0)


def issued_for(db: Session, wo_id: int) -> dict[str, float]:
    """이 작업지시에 투입한 자재(품목별, 양수)."""
    rows = db.execute(
        select(StockTxn.item_code, func.sum(StockTxn.qty))
        .where(StockTxn.wo_id == wo_id, StockTxn.txn_type == "ISSUE")
        .group_by(StockTxn.item_code)
    ).all()
    return {code: -float(q or 0) for code, q in rows}


def material_lines(db: Session, wo: WorkOrder) -> list[dict]:
    """자재 소요표: 필요 = 지시수량 × 소요량 × (1 + 손실률)."""
    issued = issued_for(db, wo.id)
    out = []
    boms = db.execute(
        select(Bom).where(Bom.parent_code == wo.item_code).order_by(Bom.component_code)
    ).scalars()
    for b in boms:
        item = db.get(Item, b.component_code)
        required = round(wo.plan_qty * b.qty_per * (1 + b.scrap_pct / 100), 6)
        done = round(issued.get(b.component_code, 0.0), 6)
        have = round(on_hand(db, b.component_code), 6)
        remain = max(0.0, required - done)
        out.append({
            "item_code": b.component_code, "name": item.name if item else None,
            "unit": item.unit if item else None, "qty_per": b.qty_per, "scrap_pct": b.scrap_pct,
            "required": required, "issued": done, "on_hand": have,
            "shortage": round(max(0.0, remain - have), 6),
        })
    return out


def good_qty_of(db: Session, wo: WorkOrder) -> int:
    """작업지시의 양품: 생산실적(검사결과)의 양품, 검사결과가 없으면 마지막으로 완료한 공정의 양품."""
    prod = summarize(records_for(db, wo))
    if prod.inspected_rows:
        return int(prod.good_qty)
    last = db.execute(
        select(WoOperation)
        .where(WoOperation.wo_id == wo.id, WoOperation.status == "DONE",
               WoOperation.good_qty.is_not(None))
        .order_by(WoOperation.seq.desc()).limit(1)
    ).scalar()
    return int(last.good_qty) if last else 0


def sync_produce_txn(db: Session, wo: WorkOrder, user: Optional[str]) -> None:
    """작업지시가 DONE 이면 양품만큼 생산 입고 1건(있으면 갱신), 아니면 그 입고를 지운다. 커밋은 부른 쪽."""
    key = f"PRODUCE:wo:{wo.id}"
    txn = db.execute(select(StockTxn).where(StockTxn.ref_key == key)).scalar()
    qty = good_qty_of(db, wo) if wo.status == "DONE" else 0
    if qty <= 0:
        if txn is not None:
            db.delete(txn)
        return
    if txn is None:
        db.add(StockTxn(ts=wo.finished_at or now_utc(), item_code=wo.item_code, lot=wo.lot,
                        qty=float(qty), txn_type="PRODUCE", wo_id=wo.id, ref_key=key,
                        note=f"작업지시 {wo.wo_no} 완료", created_by=user))
    else:
        txn.qty, txn.item_code, txn.lot = float(qty), wo.item_code, wo.lot


# ---- 설비 --------------------------------------------------------------------------

def open_event(db: Session, code: str) -> Optional[EquipmentEvent]:
    return db.execute(
        select(EquipmentEvent)
        .where(EquipmentEvent.equipment_code == code, EquipmentEvent.ended_at.is_(None))
        .order_by(EquipmentEvent.started_at.desc(), EquipmentEvent.id.desc()).limit(1)
    ).scalar()


def set_state(db: Session, code: str, state: str, *, reason: Optional[str] = None,
              wo_id: Optional[int] = None, user: Optional[str] = None) -> EquipmentEvent:
    """설비 상태를 바꾼다(진행 중인 구간을 닫고 새 구간을 연다). 같은 상태·같은 작업지시면 그대로."""
    cur = open_event(db, code)
    if cur is not None and cur.state == state and cur.wo_id == wo_id:
        if reason and reason != cur.reason:
            cur.reason = reason
        return cur
    now = now_utc()
    if cur is not None:
        cur.ended_at = now
    ev = EquipmentEvent(equipment_code=code, state=state, reason=reason or None,
                        started_at=now, wo_id=wo_id, created_by=user)
    db.add(ev)
    return ev


def utilization(db: Session, date_from: date, date_to: date) -> list[dict]:
    """기간(KST 날짜, 양끝 포함)의 설비별 상태 시간과 가동률(docs/MES.md §11.4)."""
    start = datetime.combine(date_from, time.min, tzinfo=KST).astimezone(timezone.utc)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=KST).astimezone(timezone.utc)
    end = min(end, now_utc())  # 아직 오지 않은 시간은 세지 않는다
    rows = []
    for eq in db.execute(select(Equipment).order_by(Equipment.code)).scalars():
        secs = {"RUN": 0.0, "IDLE": 0.0, "DOWN": 0.0, "PM": 0.0}
        downs = 0
        if end > start:
            evs = db.execute(
                select(EquipmentEvent).where(
                    EquipmentEvent.equipment_code == eq.code,
                    EquipmentEvent.started_at < end,
                    (EquipmentEvent.ended_at.is_(None)) | (EquipmentEvent.ended_at > start),
                )
            ).scalars()
            for ev in evs:
                a, b = max(ev.started_at, start), min(ev.ended_at or end, end)
                if b <= a or ev.state not in secs:
                    continue
                secs[ev.state] += (b - a).total_seconds()
                if ev.state == "DOWN" and ev.started_at >= start:
                    downs += 1  # 기간 안에서 시작한 고장만 횟수로 센다
        load = secs["RUN"] + secs["IDLE"] + secs["DOWN"]
        rows.append({
            "equipment_code": eq.code, "name": eq.name,
            "run_s": round(secs["RUN"], 1), "idle_s": round(secs["IDLE"], 1),
            "down_s": round(secs["DOWN"], 1), "pm_s": round(secs["PM"], 1),
            "availability_pct": round(secs["RUN"] / load * 100, 1) if load > 0 else None,
            "down_count": downs,
            "mttr_min": round(secs["DOWN"] / downs / 60, 1) if downs else None,
            "mtbf_min": round(secs["RUN"] / downs / 60, 1) if downs else None,
        })
    return rows
