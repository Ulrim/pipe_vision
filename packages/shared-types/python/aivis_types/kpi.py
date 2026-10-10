"""KPI 스키마 (CLAUDE.md §1.1, §5 M12).

산출식은 §1.1 을 그대로 구현한다(임의 변형 금지).
- 공정불량률(ppm) = (공정 중 불량수량 ÷ 총 검사수량) × 1,000,000
- 검사불량률(%)  = (오검수량 + 미검수량) ÷ 총 검사수량 × 100
- 자동검사율(%)  = AI 자동판정 완료수량 ÷ 총 검사대상수량 × 100
- 데이터 저장&MES 연계율(%) = 정상 저장·연계 건수 ÷ 전체 검사 건수 × 100
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class KpiManual(BaseModel):
    """비자동 KPI 입력 (kpi_manual 테이블, §7.1, §1.1).

    작업공수/리드타임/Claim 등 시스템이 자동 산출 불가한 항목.
    """

    period: date = Field(..., description="월 단위 기간(해당 월 1일 권장, PK)")
    claim_count: Optional[int] = Field(None, ge=0, description="Claim 건수")
    workload_index: Optional[float] = Field(None, description="작업공수 지수")
    lead_time_days: Optional[float] = Field(None, description="수주출하 리드타임(일)")
    # 출하유출불량률(계약 성과지표) 산출용. 시스템은 "출하 후 고객에서 발견된
    # 부적합"을 알 수 없으므로 이 두 값만 수기로 받는다.
    shipped_qty: Optional[int] = Field(None, ge=0, description="총 출하수량")
    leak_defect_qty: Optional[int] = Field(
        None, ge=0, description="출하 후 발견된 부적합 수량(고객 반품/클레임)"
    )
    note: Optional[str] = Field(None, description="비고")


class KpiSummary(BaseModel):
    """KPI 요약 응답 (GET /kpi/summary, §1.1).

    period 구간(월) 동안의 inspection 집계를 §1.1 산출식으로 계산한다.
    """

    period: str = Field(..., description="대상 기간(예: 2026-06)")

    total_inspected: int = Field(..., ge=0, description="총 검사수량")
    defect_count: int = Field(..., ge=0, description="공정 중 불량수량(final_verdict=NG)")
    process_defect_ppm: float = Field(
        ..., description="공정불량률(ppm) = 불량/총검사 × 1,000,000"
    )

    auto_inspected: int = Field(
        ..., ge=0, description="AI 자동판정 완료수량(final_verdict 존재)"
    )
    auto_inspection_rate_pct: float = Field(
        ..., description="자동검사율(%) = 자동판정/총검사대상 × 100"
    )

    misjudge_count: int = Field(
        ..., ge=0,
        description="오검수량 = AI 가 NG 라 했는데 사람이 다시 보니 OK(과검출)",
    )
    miss_count: int = Field(
        ..., ge=0,
        description="미검수량 = AI 가 OK 라 했는데 사람이 다시 보니 NG(놓친 불량)",
    )
    inspection_defect_rate_pct: float = Field(
        ..., description="검사불량률(%) = (오검+미검)/총검사 × 100"
    )

    stored_count: int = Field(..., ge=0, description="정상 저장 건수")
    mes_synced_count: int = Field(..., ge=0, description="MES 연계 완료 건수")
    storage_mes_rate_pct: float = Field(
        ..., description="저장&MES 연계율(%) = 저장·연계/전체 × 100"
    )

    avg_proc_time_ms: Optional[float] = Field(
        None, description="평균 처리속도(ms). 목표 ≤ 300ms/ea"
    )

    # 2026-10-10 점검 보완 — 판정 품질을 나눠 본다.
    pending_review_count: Optional[int] = Field(
        None, ge=0,
        description="재확인 대기(재확인 대상인데 아직 사람이 안 봄). 미검이 아니다",
    )
    audited_count: Optional[int] = Field(
        None, ge=0, description="사람이 재확인한 건수(오검·미검을 알 수 있는 표본)"
    )
    misjudge_rate_pct: Optional[float] = Field(None, description="오검률(%) = 오검/총검사")
    miss_rate_pct: Optional[float] = Field(None, description="미검률(%) = 미검/총검사")
    # MES 연계를 무엇으로 셌는지 — table=스테이징 적재, rest=실제 MES 응답,
    # rest_fake=가짜 전송(시험용 — 인수 증빙 아님).
    mes_mode: Optional[str] = Field(None, description="table | rest | rest_fake")
    mes_consumed_count: Optional[int] = Field(
        None, ge=0,
        description="MES 가 실제로 받아갔음이 확인된 건수(table: consumed, rest: 실 응답)",
    )
    claim_count_ytd: Optional[int] = Field(
        None, ge=0, description="Claim 연 누계(해당 연도 1월~해당 월, 수기 입력 합)"
    )

    # 비자동 입력(있으면 함께 노출)
    claim_count: Optional[int] = Field(None, description="Claim 건수(수기 입력)")
    workload_index: Optional[float] = Field(None, description="작업공수 지수(수기 입력)")
    lead_time_days: Optional[float] = Field(None, description="리드타임(일, 수기 입력)")
    shipped_qty: Optional[int] = Field(None, description="총 출하수량(수기 입력)")
    leak_defect_qty: Optional[int] = Field(
        None, description="출하 후 발견된 부적합 수량(수기 입력)"
    )
    shipment_leak_ppm: Optional[float] = Field(
        None,
        description=(
            "출하유출불량률(ppm) = 출하 후 부적합/총출하 × 1,000,000. "
            "계약 성과지표. 수기 입력(shipped_qty, leak_defect_qty) 이 있어야 산출된다."
        ),
    )
