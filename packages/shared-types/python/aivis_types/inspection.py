"""검사결과 / 기준정보 스키마 (CLAUDE.md §7.1, §5 M7,M8,M13).

InspectionResult 는 inspection 테이블(제품 1개 = 1행)과 1:1 매핑된다.
ItemMaster 는 item_master 테이블 컬럼을 그대로 미러링한다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import DefectCode, Verdict


class ItemMaster(BaseModel):
    """품목/기준정보 (item_master 테이블, §7.1)."""

    model_config = ConfigDict(use_enum_values=True)

    item_code: str = Field(..., description="품목 코드(PK)")
    item_name: str = Field(..., description="품목명")
    ref_length_mm: float = Field(..., description="기준 길이(mm)")
    tol_plus_mm: float = Field(..., description="허용 공차 +(mm)")
    tol_minus_mm: float = Field(..., description="허용 공차 -(mm)")
    px_to_mm_scale: float = Field(..., description="픽셀-mm 환산 계수(품목별 보정)")
    oil_threshold: Optional[float] = Field(None, description="유분기 임계 0~1")
    discolor_threshold: Optional[float] = Field(None, description="변색 임계 0~1")
    scratch_threshold: Optional[float] = Field(None, description="스크래치 임계 0~1")
    capture_recipe: Optional[Dict[str, Any]] = Field(
        None, description="촬영 레시피(노출/게인/조명) JSON"
    )
    version: int = Field(1, ge=1, description="기준정보 버전(변경 시 증가)")
    updated_by: Optional[str] = Field(None, description="최종 수정자")
    updated_at: Optional[datetime] = Field(None, description="최종 수정 시각")


class ItemMasterCreate(BaseModel):
    """기준정보 등록 입력(version/updated_* 제외)."""

    model_config = ConfigDict(use_enum_values=True)

    item_code: str
    item_name: str
    ref_length_mm: float
    tol_plus_mm: float
    tol_minus_mm: float
    px_to_mm_scale: float
    oil_threshold: Optional[float] = None
    discolor_threshold: Optional[float] = None
    scratch_threshold: Optional[float] = None
    capture_recipe: Optional[Dict[str, Any]] = None


class ItemMasterUpdate(BaseModel):
    """기준정보 수정 입력(부분 갱신). 변경 시 version 자동 증가."""

    model_config = ConfigDict(use_enum_values=True)

    item_name: Optional[str] = None
    ref_length_mm: Optional[float] = None
    tol_plus_mm: Optional[float] = None
    tol_minus_mm: Optional[float] = None
    px_to_mm_scale: Optional[float] = None
    oil_threshold: Optional[float] = None
    discolor_threshold: Optional[float] = None
    scratch_threshold: Optional[float] = None
    capture_recipe: Optional[Dict[str, Any]] = None


class InspectionResult(BaseModel):
    """검사 결과 (inspection 테이블, 제품 1개 = 1행, §7.1).

    비전 워커가 POST /inspection 으로 적재하는 본문 + DB 조회 응답에 공통 사용.
    id/mes_synced 는 적재 시 미지정(서버가 채움), 조회 응답엔 포함.
    """

    model_config = ConfigDict(use_enum_values=True)

    id: Optional[int] = Field(None, description="PK(BIGSERIAL). 적재 시 미지정")

    # 식별/메타 (§7.1, M7)
    lot: str = Field(..., description="LOT 번호")
    work_order: Optional[str] = Field(None, description="작업지시 번호")
    item_code: str = Field(..., description="품목 코드(item_master FK)")
    cam_id: str = Field(..., description="카메라 ID")
    inspected_at: datetime = Field(..., description="검사 시각")
    shift: Optional[str] = Field(None, description="작업 교대")
    operator: Optional[str] = Field(None, description="작업자")

    # 길이
    ref_length_mm: Optional[float] = Field(None, description="기준 길이(mm)")
    meas_length_mm: Optional[float] = Field(None, description="측정 길이(mm)")
    deviation_mm: Optional[float] = Field(None, description="편차(mm)")
    length_verdict: Optional[Verdict] = Field(None, description="길이 판정 OK/NG")

    # 표면 (0~1 신뢰도)
    oil_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="유분기 신뢰도")
    discolor_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="변색 신뢰도")
    scratch_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="스크래치 신뢰도")

    # 종합
    final_verdict: Verdict = Field(..., description="최종 판정 OK/NG")
    defect_codes: List[DefectCode] = Field(
        default_factory=list, description="불량유형 코드 배열(§7.2)"
    )
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="종합 신뢰도")

    raw_image_path: Optional[str] = Field(None, description="원본 이미지 경로(MinIO/NAS)")
    result_image_path: Optional[str] = Field(None, description="판정 오버레이 이미지 경로")
    proc_time_ms: Optional[int] = Field(
        None, ge=0, description="이미지 취득~저장 처리시간(ms). 처리속도 KPI"
    )

    # 운영/재확인
    review_flag: bool = Field(False, description="오검/미검 후보(재확인 대상)")
    manual_verdict: Optional[Verdict] = Field(None, description="작업자 재확인 결과")
    mes_synced: bool = Field(False, description="MES 연계 완료 여부")


class ReviewUpdate(BaseModel):
    """검사결과 재확인 입력 (PATCH /inspection/{id}/review, M10)."""

    model_config = ConfigDict(use_enum_values=True)

    manual_verdict: Verdict = Field(..., description="작업자 재확인 판정 OK/NG")
    review_flag: Optional[bool] = Field(
        None, description="재확인 처리 후 플래그(미지정 시 False로 해제)"
    )
    operator: Optional[str] = Field(None, description="재확인 수행 작업자")


class InspectionImages(BaseModel):
    """검사 이미지 경로 응답 (GET /inspection/{id}/images, M8)."""

    id: int
    raw_image_path: Optional[str] = None
    result_image_path: Optional[str] = None
