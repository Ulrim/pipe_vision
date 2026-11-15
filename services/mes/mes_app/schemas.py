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


# ==== 2026-11-15 범위 확대: 기준정보·BOM / 공정 라우팅·공정 진행 / 자재·재고 / 설비 가동 ====
# docs/MES.md §11. 대시보드 `apps/dashboard/src/api/mesPlant.ts` 와 1:1.

ItemType = Literal["RAW", "FG", "SUB"]            # 원자재 | 제품 | 부자재
OpStatus = Literal["PENDING", "RUNNING", "DONE", "SKIPPED"]
EquipState = Literal["RUN", "IDLE", "DOWN", "PM"]  # 가동 | 대기 | 고장 | 계획정지(점검)
TxnType = Literal["RECEIPT", "ISSUE", "PRODUCE", "SHIP", "ADJUST"]


# ---- 품목·BOM ---------------------------------------------------------------------

class ItemIn(BaseModel):
    item_code: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    item_type: ItemType = "FG"
    unit: str = Field("EA", min_length=1)
    safety_stock: float = Field(0, ge=0, description="안전재고 — 이보다 적으면 재고 화면에 경고")
    spec: Optional[str] = None


class ItemPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    item_type: Optional[ItemType] = None
    unit: Optional[str] = Field(None, min_length=1)
    safety_stock: Optional[float] = Field(None, ge=0)
    spec: Optional[str] = None
    active: Optional[bool] = None


class ItemOut(_Out):
    item_code: str
    name: str
    item_type: str
    unit: str
    safety_stock: float
    spec: Optional[str]
    active: bool
    created_at: Optional[datetime]


class BomLineIn(BaseModel):
    component_code: str = Field(..., min_length=1)
    qty_per: float = Field(..., gt=0, description="제품 1개당 소요량(자재 단위)")
    scrap_pct: float = Field(0, ge=0, lt=100, description="손실률 % — 소요량에 더한다")
    note: Optional[str] = None


class BomIn(BaseModel):
    lines: list[BomLineIn]


class BomLineOut(BaseModel):
    parent_code: str
    component_code: str
    component_name: Optional[str] = None
    unit: Optional[str] = None
    qty_per: float
    scrap_pct: float
    note: Optional[str] = None


# ---- 공정·라우팅 -------------------------------------------------------------------

class ProcessIn(BaseModel):
    code: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    seq: int = Field(..., ge=0, description="기본 공정 순서")
    auto_from_inspection: bool = Field(
        False, description="검사 공정 — 양품·불량을 AIVIS 검사결과에서 가져온다(손으로 넣지 않는다)")


class ProcessPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    seq: Optional[int] = Field(None, ge=0)
    auto_from_inspection: Optional[bool] = None
    active: Optional[bool] = None


class ProcessOut(_Out):
    code: str
    name: str
    seq: int
    auto_from_inspection: bool
    active: bool


class RoutingStepIn(BaseModel):
    process_code: str = Field(..., min_length=1)
    equipment_code: Optional[str] = None
    std_sec_per_ea: Optional[float] = Field(None, ge=0, description="표준 작업시간(초/개)")


class RoutingIn(BaseModel):
    steps: list[RoutingStepIn]


class RoutingStepOut(BaseModel):
    seq: int
    process_code: str
    process_name: str
    equipment_code: Optional[str] = None
    std_sec_per_ea: Optional[float] = None


class RoutingOut(BaseModel):
    item_code: str
    # item = 이 품목 전용 / default = 공통 라우팅("*") / processes = 라우팅 없음 → 사용 중인 공정 전부
    source: Literal["item", "default", "processes"]
    steps: list[RoutingStepOut]


# ---- 설비·가동 ---------------------------------------------------------------------

class EquipmentIn(BaseModel):
    code: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    process_code: Optional[str] = None
    note: Optional[str] = None


class EquipmentPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    process_code: Optional[str] = None
    note: Optional[str] = None
    active: Optional[bool] = None


class EquipmentOut(BaseModel):
    code: str
    name: str
    process_code: Optional[str]
    note: Optional[str]
    active: bool
    state: Optional[EquipState] = None       # 기록이 한 번도 없으면 None
    state_since: Optional[datetime] = None
    state_reason: Optional[str] = None
    wo_id: Optional[int] = None              # 지금 이 설비에서 돌고 있는 작업지시
    wo_no: Optional[str] = None


class EquipmentStateIn(BaseModel):
    state: EquipState
    reason: Optional[str] = Field(None, description="DOWN·PM 은 사유를 적는다(필수)")


class EquipmentEventOut(_Out):
    id: int
    equipment_code: str
    state: str
    reason: Optional[str]
    started_at: datetime
    ended_at: Optional[datetime]
    wo_id: Optional[int]
    created_by: Optional[str]


class UtilizationRow(BaseModel):
    equipment_code: str
    name: str
    run_s: float
    idle_s: float
    down_s: float
    pm_s: float
    # 가동률 = 가동 ÷ (가동 + 대기 + 고장). 계획정지(PM)와 기록 없는 시간은 분모에서 뺀다.
    availability_pct: Optional[float]
    down_count: int
    mttr_min: Optional[float]   # 평균 수리시간 = 고장시간 ÷ 고장 횟수
    mtbf_min: Optional[float]   # 평균 고장간격 = 가동시간 ÷ 고장 횟수


class UtilizationOut(BaseModel):
    date_from: date
    date_to: date
    rows: list[UtilizationRow]


# ---- 공정 진행(작업지시의 공정별 실적) ---------------------------------------------------

class OperationOut(_Out):
    id: int
    wo_id: int
    seq: int
    process_code: str
    process_name: str
    equipment_code: Optional[str]
    status: str
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    good_qty: Optional[int]
    defect_qty: Optional[int]
    worker: Optional[str]
    note: Optional[str]
    auto: bool = False                     # 검사 공정 — 아래 두 값이 AIVIS 검사결과에서 온다
    auto_good_qty: Optional[int] = None
    auto_defect_qty: Optional[int] = None


class OpStartIn(BaseModel):
    equipment_code: Optional[str] = None
    worker: Optional[str] = None


class OpCompleteIn(BaseModel):
    good_qty: Optional[int] = Field(None, ge=0, description="검사 공정은 비워도 된다")
    defect_qty: int = Field(0, ge=0)
    note: Optional[str] = None


# ---- 자재 소요·투입, 재고 ------------------------------------------------------------

class MaterialLine(BaseModel):
    item_code: str
    name: Optional[str] = None
    unit: Optional[str] = None
    qty_per: float
    scrap_pct: float
    required: float     # 지시수량 × 소요량 × (1 + 손실률)
    issued: float       # 이 작업지시에 이미 투입한 양
    on_hand: float      # 현재고
    shortage: float     # max(0, 남은 필요량 − 현재고)


class MaterialsOut(BaseModel):
    wo_id: int
    plan_qty: int
    lines: list[MaterialLine]


class IssueLineIn(BaseModel):
    item_code: str = Field(..., min_length=1)
    qty: float = Field(..., gt=0)
    lot: Optional[str] = Field(None, description="투입한 자재 LOT(추적용)")


class MaterialIssueIn(BaseModel):
    lines: Optional[list[IssueLineIn]] = Field(
        None, description="비우면 BOM 기준 남은 필요량을 전부 투입한다")
    allow_negative: bool = Field(False, description="재고가 모자라도 투입 기록(재고가 음수가 된다)")


class StockRow(BaseModel):
    item_code: str
    name: Optional[str] = None
    item_type: Optional[str] = None
    unit: Optional[str] = None
    on_hand: float
    safety_stock: float = 0
    below_safety: bool = False
    last_txn_at: Optional[datetime] = None


class StockTxnOut(_Out):
    id: int
    ts: datetime
    item_code: str
    lot: Optional[str]
    qty: float            # 부호 있음: 입고·생산 +, 투입·출하 −
    txn_type: str
    wo_id: Optional[int]
    wo_no: Optional[str] = None
    shipment_id: Optional[int]
    note: Optional[str]
    created_by: Optional[str]


class ReceiptIn(BaseModel):
    item_code: str = Field(..., min_length=1)
    qty: float = Field(..., gt=0)
    lot: Optional[str] = None
    note: Optional[str] = None


class AdjustIn(BaseModel):
    item_code: str = Field(..., min_length=1)
    qty: float = Field(..., description="더하거나 빼는 양(0 제외)")
    lot: Optional[str] = None
    note: str = Field(..., min_length=1, description="조정 사유(필수)")
