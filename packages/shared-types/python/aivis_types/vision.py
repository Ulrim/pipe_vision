"""비전 파이프라인 중간/종합 출력 스키마 (CLAUDE.md §4, §5 M3~M5, §6).

vision-ai 에이전트가 import 하여 각 단계 결과를 반환한다.
- LengthResult : 길이 측정 모듈(M3) 출력
- SurfaceResult: 표면 결함 판정 모듈(M4) 출력
- VerdictResult: 종합 판정 모듈(M5) 출력 — InspectionResult 적재의 직전 단계

모든 추론 출력은 결정적이며 proc_time_ms 를 계측해 반환한다(vision-ai 원칙).
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import DefectCode, Verdict


class LengthResult(BaseModel):
    """길이 측정 결과 (M3). px-mm 환산은 item_master.px_to_mm_scale 사용."""

    model_config = ConfigDict(use_enum_values=True)

    ref_length_mm: float = Field(..., description="기준 길이(mm), item_master.ref_length_mm")
    meas_length_mm: Optional[float] = Field(
        None, description="측정 길이(mm). 끝단 검출 실패 시 None"
    )
    deviation_mm: Optional[float] = Field(
        None, description="편차 = meas_length_mm - ref_length_mm"
    )
    length_verdict: Verdict = Field(..., description="공차 판정 OK/NG")
    edge_detected: bool = Field(
        True, description="양 끝단 검출 성공 여부. False면 오류 알림 대상(M3 DoD)"
    )
    proc_time_ms: int = Field(0, ge=0, description="이 단계 처리시간(ms)")


class SurfaceResult(BaseModel):
    """표면 결함 판정 결과 (M4). 각 점수는 0~1 신뢰도."""

    model_config = ConfigDict(use_enum_values=True)

    oil_score: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="유분기 신뢰도 0~1"
    )
    discolor_score: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="변색 신뢰도 0~1"
    )
    scratch_score: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="스크래치 신뢰도 0~1"
    )
    surface_verdict: Verdict = Field(
        ..., description="표면 종합 OK/NG (임계값 초과 항목 존재 시 NG)"
    )
    defect_codes: List[DefectCode] = Field(
        default_factory=list,
        description="표면 기인 불량 코드 부분집합 {OIL,DIS,SCR}",
    )
    proc_time_ms: int = Field(0, ge=0, description="이 단계 처리시간(ms)")


class VerdictResult(BaseModel):
    """종합 판정 결과 (M5). 길이+표면 통합 → 최종 OK/NG + 불량유형 코드.

    이 스키마는 InspectionResult 적재(POST /inspection) 직전의 비전 최종 산출물이다.
    pipeline.py 가 이를 채워 backend 로 전달한다.
    """

    model_config = ConfigDict(use_enum_values=True)

    final_verdict: Verdict = Field(..., description="최종 OK/NG")
    defect_codes: List[DefectCode] = Field(
        default_factory=list,
        description="불량유형 코드 배열. 2종 이상이면 MULTI 포함(§7.2)",
    )
    confidence: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="종합 신뢰도 0~1"
    )
    review_flag: bool = Field(
        False, description="오검/미검 후보(재확인 대상) 자동 분류 결과(M5/M16)"
    )
    length: LengthResult = Field(..., description="길이 측정 상세")
    surface: SurfaceResult = Field(..., description="표면 판정 상세")
    proc_time_ms: int = Field(
        0, ge=0, description="이미지 취득~판정까지 누적 처리시간(ms). KPI 300ms 대상"
    )
