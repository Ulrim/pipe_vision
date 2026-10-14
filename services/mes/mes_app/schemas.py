"""MES API 스키마 (docs/MES.md §6). 대시보드 `apps/dashboard/src/api/mes.ts` 와 1:1."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SoStatus = Literal["OPEN", "CLOSED"]
WoStatus = Literal["PLANNED", "RUNNING", "DONE", "CANCELLED"]
ClaimStatus = Literal["OPEN", "CLOSED"]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- 수주 ------------------------------------------------------------------------

class SalesOrderIn(BaseModel):
    so_no: Optional[str] = Field(None, description="비우면 SO-YYYYMMDD-NNN 자동")
    customer: str = Field(..., min_length=1)
    item_code: str = Field(..., min_length=1)
    qty: int = Field(..., gt=0)
    order_date: date
    due_date: Optional[date] = None
    note: Optional[str] = None


class SalesOrderPatch(BaseModel):
    status: Optional[SoStatus] = None
    qty: Optional[int] = Field(None, gt=0)
    due_date: Optional[date] = None
    note: Optional[str] = None


class SalesOrderOut(_Out):
    id: int
    so_no: str
    customer: str
    item_code: str
    qty: int
    order_date: date
    due_date: Optional[date]
    status: str
    note: Optional[str]
    created_by: Optional[str]
    created_at: Optional[datetime]
    shipped_qty: int = 0
    work_order_count: int = 0


# ---- 작업지시 ----------------------------------------------------------------------

class WorkOrderIn(BaseModel):
    wo_no: Optional[str] = Field(None, description="비우면 WO-YYYYMMDD-NNN 자동")
    so_id: Optional[int] = None
    item_code: Optional[str] = Field(None, description="수주를 고르면 비워도 된다")
    lot: Optional[str] = Field(None, description="비우면 L-YYYYMMDD-NN 자동. 작업지시마다 하나")
    plan_qty: int = Field(..., gt=0)
    plan_date: Optional[date] = None
    note: Optional[str] = None


class WorkOrderPatch(BaseModel):
    status: Optional[WoStatus] = None
    plan_qty: Optional[int] = Field(None, gt=0)
    plan_date: Optional[date] = None
    note: Optional[str] = None


class StageStatOut(BaseModel):
    stage: str
    label: str
    inspected: int
    ai_ng: int
    final_ng: int
    reviewed: int
    good: int
    by_code: dict[str, int]


class ProductionOut(BaseModel):
    inspected_rows: int
    stages: list[StageStatOut]
    defect_total: int
    by_code: dict[str, int]
    good_qty: int
    good_source: str
    crate_count: int
    crate_tubes: int
    crate_mismatch: int
    first_at: Optional[datetime]
    last_at: Optional[datetime]


class WorkOrderOut(_Out):
    id: int
    wo_no: str
    so_id: Optional[int]
    so_no: Optional[str] = None
    customer: Optional[str] = None
    item_code: str
    lot: str
    plan_qty: int
    plan_date: Optional[date]
    status: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    note: Optional[str]
    created_by: Optional[str]
    created_at: Optional[datetime]
    # 요약(목록 화면) — 상세는 production
    good_qty: int = 0
    defect_total: int = 0
    inspected_rows: int = 0
    progress_pct: Optional[float] = None
    last_at: Optional[datetime] = None


class WorkOrderDetail(BaseModel):
    work_order: WorkOrderOut
    production: ProductionOut


class UnassignedLot(BaseModel):
    lot: str
    work_order: Optional[str]
    item_code: Optional[str]
    production: ProductionOut


# ---- 출하·클레임 -------------------------------------------------------------------

class ShipmentIn(BaseModel):
    ship_no: Optional[str] = None
    so_id: Optional[int] = None
    lot: Optional[str] = None
    item_code: Optional[str] = Field(None, description="수주를 고르면 비워도 된다")
    customer: Optional[str] = Field(None, description="수주를 고르면 비워도 된다")
    qty: int = Field(..., gt=0)
    ship_date: date
    note: Optional[str] = None


class ShipmentOut(_Out):
    id: int
    ship_no: str
    so_id: Optional[int]
    so_no: Optional[str] = None
    lot: Optional[str]
    item_code: str
    customer: str
    qty: int
    ship_date: date
    note: Optional[str]
    created_by: Optional[str]
    created_at: Optional[datetime]
    lead_time_days: Optional[int] = None
    on_time: Optional[bool] = None


class ClaimIn(BaseModel):
    claim_no: Optional[str] = None
    customer: str = Field(..., min_length=1)
    lot: Optional[str] = None
    item_code: Optional[str] = None
    defect_qty: int = Field(0, ge=0)
    defect_type: Optional[str] = Field(None, description="LEN|OIL|DIS|SCR|COUNT|ETC")
    occurred_date: date
    description: Optional[str] = None


class ClaimPatch(BaseModel):
    status: Optional[ClaimStatus] = None
    defect_qty: Optional[int] = Field(None, ge=0)
    defect_type: Optional[str] = None
    description: Optional[str] = None


class ClaimOut(_Out):
    id: int
    claim_no: str
    customer: str
    lot: Optional[str]
    item_code: Optional[str]
    defect_qty: int
    defect_type: Optional[str]
    occurred_date: date
    description: Optional[str]
    status: str
    closed_at: Optional[datetime]
    created_by: Optional[str]
    created_at: Optional[datetime]


class MonthlyKpi(BaseModel):
    period: str
    shipment_count: int
    shipped_qty: int
    lead_time_days: Optional[float]
    lead_time_samples: int
    on_time_rate_pct: Optional[float]
    on_time_samples: int
    claim_count: int
    leak_defect_qty: int
    leak_ppm: Optional[float]


# ---- 수신 ------------------------------------------------------------------------

class QualityIn(BaseModel):
    """AIVIS REST 연계 본문(docs/MES_INTERFACE.md §3.1). 모르는 키는 무시한다."""

    model_config = ConfigDict(extra="ignore")

    idem_key: Optional[str] = None
    inspection_id: Optional[int] = None
    lot: str
    item_code: Optional[str] = None
    inspected_at: datetime
    cam_id: Optional[str] = None
    work_order: Optional[str] = None
    inspection_stage: Optional[str] = None
    tube_index: Optional[int] = None
    final_verdict: str
    defect_codes: Optional[list[str]] = None
    meas_length_mm: Optional[float] = None
    deviation_mm: Optional[float] = None
    manual_verdict: Optional[str] = None
    count_detected: Optional[int] = None
    count_expected: Optional[int] = None


class QualityOut(_Out):
    id: int
    idem_key: str
    inspection_id: Optional[int]
    lot: str
    work_order: Optional[str]
    item_code: Optional[str]
    cam_id: Optional[str]
    inspection_stage: Optional[str]
    tube_index: Optional[int]
    inspected_at: datetime
    final_verdict: str
    defect_codes: Optional[list[str]]
    meas_length_mm: Optional[float]
    deviation_mm: Optional[float]
    manual_verdict: Optional[str]
    count_detected: Optional[int]
    count_expected: Optional[int]
    source: str
    received_at: Optional[datetime]
    updated_at: Optional[datetime]


class IntakeRunOut(_Out):
    ts: datetime
    source: str
    received: int
    updated: int
    error: Optional[str]


class PollOut(BaseModel):
    scanned: int
    stored: int
    updated: int
    duplicate: int
    marked: int
    error: Optional[str]


class IntakeStatus(BaseModel):
    table_enabled: bool
    poller_running: bool
    poll_interval_s: float
    last_poll_at: Optional[datetime]
    last_result: Optional[PollOut]
    interface_pending: Optional[int] = Field(
        None, description="AIVIS 인터페이스 테이블에서 아직 안 가져간 건수"
    )
    interface_error: Optional[str] = None
    rest_token_required: bool
    records_total: int
    records_today: int
    last_received_at: Optional[datetime]
    runs: list[IntakeRunOut]
