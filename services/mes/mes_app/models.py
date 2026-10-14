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
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
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
