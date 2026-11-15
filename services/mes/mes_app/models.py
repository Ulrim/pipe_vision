"""MES 테이블 (docs/MES.md §3).

흐름: 수주(sales order) → 작업지시(work order, LOT 하나) → AIVIS 검사결과 수신
(quality record) → 생산실적(집계, 저장하지 않음) → 출하(shipment) → 클레임(claim).

생산실적을 따로 저장하지 않는 이유: 재확인 결과가 나중에 바뀌면(AIVIS 가 같은
멱등키로 다시 보낸다) 저장해 둔 실적이 틀어진다. 품질이력에서 매번 센다.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from mes_app.db import Base, UTCDateTime


class SalesOrder(Base):
    """수주. 리드타임(수주일→출하일)과 납기 준수의 기준."""

    __tablename__ = "mes_sales_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    so_no: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    customer: Mapped[str] = mapped_column(Text, nullable=False)
    item_code: Mapped[str] = mapped_column(Text, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="OPEN")  # OPEN|CLOSED
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )


class WorkOrder(Base):
    """작업지시. LOT 하나 = 작업지시 하나(검사결과를 LOT 으로도 찾는다)."""

    __tablename__ = "mes_work_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wo_no: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    so_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("mes_sales_order.id"))
    item_code: Mapped[str] = mapped_column(Text, nullable=False)
    lot: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    plan_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_date: Mapped[date | None] = mapped_column(Date)
    # PLANNED → RUNNING(검사결과가 처음 들어오면 자동) → DONE | CANCELLED
    status: Mapped[str] = mapped_column(Text, nullable=False, default="PLANNED")
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )


class QualityRecord(Base):
    """AIVIS 에서 받은 검사결과 1건(제품 1개, 또는 크레이트 1판). 멱등키로 1행."""

    __tablename__ = "mes_quality_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idem_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    inspection_id: Mapped[int | None] = mapped_column(Integer)
    lot: Mapped[str] = mapped_column(Text, nullable=False)
    work_order: Mapped[str | None] = mapped_column(Text)
    item_code: Mapped[str | None] = mapped_column(Text)
    cam_id: Mapped[str | None] = mapped_column(Text)
    inspection_stage: Mapped[str | None] = mapped_column(Text)
    tube_index: Mapped[int | None] = mapped_column(Integer)
    inspected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    final_verdict: Mapped[str] = mapped_column(Text, nullable=False)
    defect_codes: Mapped[list | None] = mapped_column(JSON)
    meas_length_mm: Mapped[float | None] = mapped_column(Float)
    deviation_mm: Mapped[float | None] = mapped_column(Float)
    manual_verdict: Mapped[str | None] = mapped_column(Text)
    count_detected: Mapped[int | None] = mapped_column(Integer)
    count_expected: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # table | rest
    received_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_mesqr_lot", "lot"),
        Index("ix_mesqr_wo", "work_order"),
        Index("ix_mesqr_time", "inspected_at"),
    )


class Shipment(Base):
    """출하. 월 출하수량·리드타임·납기 준수의 근거."""

    __tablename__ = "mes_shipment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ship_no: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    so_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("mes_sales_order.id"))
    lot: Mapped[str | None] = mapped_column(Text)
    item_code: Mapped[str] = mapped_column(Text, nullable=False)
    customer: Mapped[str] = mapped_column(Text, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    ship_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )


class Claim(Base):
    """고객 클레임(출하 후 발견된 부적합). 월 Claim 건수·출하유출 부적합 수량의 근거."""

    __tablename__ = "mes_claim"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_no: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    customer: Mapped[str] = mapped_column(Text, nullable=False)
    lot: Mapped[str | None] = mapped_column(Text)
    item_code: Mapped[str | None] = mapped_column(Text)
    defect_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    defect_type: Mapped[str | None] = mapped_column(Text)  # LEN|OIL|DIS|SCR|COUNT|ETC
    occurred_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="OPEN")  # OPEN|CLOSED
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), server_default=func.now()
    )


class IntakeRun(Base):
    """수신 실행 기록(연계 현황 화면). 받은 것이 있거나 오류가 난 회차만 남긴다."""

    __tablename__ = "mes_intake_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # table | rest
    received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)


# ==== 2026-11-15 범위 확대 (docs/MES.md §11) ============================================

class Item(Base):
    """품목 기준정보(원자재 RAW · 제품 FG · 부자재 SUB). 제품 코드는 AIVIS 품목 코드와 같게 쓴다."""

    __tablename__ = "mes_item"

    item_code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    item_type: Mapped[str] = mapped_column(Text, nullable=False, default="FG")
    unit: Mapped[str] = mapped_column(Text, nullable=False, default="EA")
    safety_stock: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    spec: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), server_default=func.now())


class Bom(Base):
    """BOM 한 줄: 제품(parent) 1개에 자재(component)가 qty_per 만큼 든다."""

    __tablename__ = "mes_bom"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_code: Mapped[str] = mapped_column(Text, nullable=False)
    component_code: Mapped[str] = mapped_column(Text, nullable=False)
    qty_per: Mapped[float] = mapped_column(Float, nullable=False)
    scrap_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("parent_code", "component_code", name="uq_mesbom_pair"),)


class Process(Base):
    """공정. auto_from_inspection = 검사 공정(양품·불량을 AIVIS 검사결과에서 가져온다)."""

    __tablename__ = "mes_process"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    auto_from_inspection: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Routing(Base):
    """라우팅 한 단계. item_code 가 '*' 면 공통 라우팅."""

    __tablename__ = "mes_routing"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_code: Mapped[str] = mapped_column(Text, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    process_code: Mapped[str] = mapped_column(Text, nullable=False)
    equipment_code: Mapped[str | None] = mapped_column(Text)
    std_sec_per_ea: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (UniqueConstraint("item_code", "seq", name="uq_mesrouting_seq"),)


class Equipment(Base):
    """설비. 현재 상태는 저장하지 않고 마지막 상태 구간(EquipmentEvent)에서 읽는다."""

    __tablename__ = "mes_equipment"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    process_code: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class EquipmentEvent(Base):
    """설비 상태 구간 하나(RUN 가동 · IDLE 대기 · DOWN 고장 · PM 계획정지). 진행 중이면 ended_at NULL."""

    __tablename__ = "mes_equipment_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    equipment_code: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    wo_id: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_meseqev_eq_time", "equipment_code", "started_at"),)


class WoOperation(Base):
    """작업지시의 공정 하나. 작업지시를 만들 때 라우팅에서 복사한다."""

    __tablename__ = "mes_wo_operation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wo_id: Mapped[int] = mapped_column(Integer, ForeignKey("mes_work_order.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    process_code: Mapped[str] = mapped_column(Text, nullable=False)
    process_name: Mapped[str] = mapped_column(Text, nullable=False)
    equipment_code: Mapped[str | None] = mapped_column(Text)
    # PENDING → RUNNING → DONE | SKIPPED
    status: Mapped[str] = mapped_column(Text, nullable=False, default="PENDING")
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    good_qty: Mapped[int | None] = mapped_column(Integer)
    defect_qty: Mapped[int | None] = mapped_column(Integer)
    worker: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("wo_id", "seq", name="uq_meswoop_seq"),)


class StockTxn(Base):
    """수불(재고 이동) 1건. 현재고 = 품목별 qty 합(저장하지 않는다).

    qty 는 부호가 있다: RECEIPT 입고 +, PRODUCE 생산 입고 +, ISSUE 투입 −, SHIP 출하 −, ADJUST ±.
    ref_key 는 자동 기록(작업지시 완료·출하)이 두 번 들어가지 않게 하는 열쇠다.
    """

    __tablename__ = "mes_stock_txn"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    item_code: Mapped[str] = mapped_column(Text, nullable=False)
    lot: Mapped[str | None] = mapped_column(Text)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    txn_type: Mapped[str] = mapped_column(Text, nullable=False)
    wo_id: Mapped[int | None] = mapped_column(Integer)
    shipment_id: Mapped[int | None] = mapped_column(Integer)
    ref_key: Mapped[str | None] = mapped_column(Text, unique=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_messtock_item", "item_code"),
        Index("ix_messtock_wo", "wo_id"),
    )
