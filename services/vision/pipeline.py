"""검사 오케스트레이션 (CLAUDE.md §4 7단계 중 ①~④).

trigger → grab → preprocess → length + surface → verdict.
각 단계 proc_time 을 합산해 전체 proc_time_ms 를 계측한다(목표 <300ms).

ItemMaster 는 주입받는다(DB 직접접근 X — dict/스키마로 받음).
최종 산출물은 VerdictResult 이며, backend POST /inspection 본문
(InspectionResult)으로 변환하는 매핑 함수를 제공한다(HTTP 전송은 옵션/스텁).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

import numpy as np
from aivis_types import (
    DefectCode,
    InspectionResult,
    ItemMaster,
    LengthResult,
    SurfaceResult,
    Verdict,
    VerdictResult,
)

from .length import measure_length
from .preprocess import preprocess
from .surface.model import ClassicalSurfaceModel, SurfaceModel
from .verdict import combine_verdict


def _to_item(item: Union[ItemMaster, Dict[str, Any]]) -> ItemMaster:
    if isinstance(item, ItemMaster):
        return item
    return ItemMaster(**item)


def _error_verdict(
    item: ItemMaster,
    *,
    proc_time_ms: int,
    defect_codes: Optional[List[DefectCode]] = None,
) -> VerdictResult:
    """예외/실패 시에도 미판정 없이 결정적 NG 를 만든다(자동검사율 100%).

    길이·표면을 '검출 실패' 상태로 채우고 review_flag=True(재확인 대상)로 둔다.
    defect_codes 가 없으면 길이·표면 모두 불명이므로 MULTI 로 표기한다.
    """
    ref = float(item.ref_length_mm)
    length = LengthResult(
        ref_length_mm=ref,
        meas_length_mm=None,
        deviation_mm=None,
        length_verdict=Verdict.NG,
        edge_detected=False,
        proc_time_ms=0,
    )
    surface = SurfaceResult(
        oil_score=None,
        discolor_score=None,
        scratch_score=None,
        surface_verdict=Verdict.NG,
        defect_codes=[],
        proc_time_ms=0,
    )
    codes = defect_codes if defect_codes else [DefectCode.MULTI]
    return VerdictResult(
        final_verdict=Verdict.NG,
        defect_codes=codes,
        confidence=0.0,
        review_flag=True,
        length=length,
        surface=surface,
        proc_time_ms=proc_time_ms,
    )


@dataclass
class StageTimings:
    """단계별 처리시간(ms) 분해 — 처리속도 KPI 회귀/병목 분석용."""

    preprocess_ms: int = 0
    length_ms: int = 0
    surface_ms: int = 0
    verdict_ms: int = 0
    total_ms: int = 0


@dataclass
class InspectionPipeline:
    """단일 프레임 검사 파이프라인.

    surface_model 을 교체하면(고전 CV → ONNX) 파이프라인 변경 없이 고도화.
    """

    surface_model: SurfaceModel = field(default_factory=ClassicalSurfaceModel)
    surface_inset_ratio: float = 0.06

    def run(
        self,
        frame_bgr: np.ndarray,
        item: Union[ItemMaster, Dict[str, Any]],
    ) -> VerdictResult:
        """프레임 1장 → VerdictResult. 결정적, 전체 proc_time_ms 계측.

        어떤 입력/예외에도 미판정 없이 VerdictResult 를 반환한다(자동검사율 100%).
        """
        result, _, _ = self._run_core(frame_bgr, item)
        return result

    def run_with_timings(
        self,
        frame_bgr: np.ndarray,
        item: Union[ItemMaster, Dict[str, Any]],
    ) -> tuple[VerdictResult, StageTimings]:
        """run() 과 동일하나 단계별 타이밍 분해를 함께 반환."""
        result, timings, _ = self._run_core(frame_bgr, item)
        return result, timings

    def run_safe(
        self,
        frame_bgr: np.ndarray,
        item: Union[ItemMaster, Dict[str, Any]],
    ) -> tuple[VerdictResult, Optional[str]]:
        """run() + 오류 사유. 결코 raise 하지 않는다.

        반환: (VerdictResult, error_reason). 정상 처리 시 error_reason=None.
        shared-types 스키마(VerdictResult)는 변경하지 않으므로, 사유는 별도
        채널로 반환한다(상위에서 sys_log/HMI 알람에 연결).
        """
        result, _, err = self._run_core(frame_bgr, item)
        return result, err

    # --- 내부: 단계별 예외 격리 + 결정적 NG 폴백 ---
    def _run_core(
        self,
        frame_bgr: np.ndarray,
        item: Union[ItemMaster, Dict[str, Any]],
    ) -> tuple[VerdictResult, StageTimings, Optional[str]]:
        t0 = time.perf_counter()
        # ItemMaster 변환 실패는 호출자 오류이므로 그대로 raise(검사 불가).
        master = _to_item(item)

        # ② 전처리 — 실패해도 검사 자체는 NG 로 종료(미판정 0).
        try:
            pre = preprocess(frame_bgr, self.surface_inset_ratio)
        except Exception as exc:  # noqa: BLE001
            total = int(round((time.perf_counter() - t0) * 1000))
            reason = f"preprocess 실패: {type(exc).__name__}: {exc}"
            result = _error_verdict(master, proc_time_ms=total)
            return result, StageTimings(total_ms=total), reason

        errors: List[str] = []

        # ③ 길이 측정 — 실패 시 끝단검출 실패(NG) 로 격리.
        try:
            if pre.length_roi is not None:
                gray_roi = pre.length_roi.crop(pre.gray_corrected)
            else:
                gray_roi = pre.gray_corrected  # ROI 미검출 → 끝단검출 실패 유도.
            length = measure_length(gray_roi, master)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"length 실패: {type(exc).__name__}: {exc}")
            length = LengthResult(
                ref_length_mm=float(master.ref_length_mm),
                meas_length_mm=None,
                deviation_mm=None,
                length_verdict=Verdict.NG,
                edge_detected=False,
                proc_time_ms=0,
            )

        # ③ 표면 판정 — 추론 실패 시 NG(표면 불명) 로 격리.
        try:
            if pre.surface_roi is not None:
                region = pre.surface_roi.crop(frame_bgr)
                region_mask = pre.surface_roi.crop(pre.mask)
            else:
                region = frame_bgr
                region_mask = pre.mask
            surface = self.surface_model.predict(region, master, mask=region_mask)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"surface 실패: {type(exc).__name__}: {exc}")
            # 표면 불명: 특정 결함을 단정하지 않는다(MULTI 날조 금지).
            # 최종 override 로 NG+review 가 강제되므로 미판정은 없다.
            surface = SurfaceResult(
                oil_score=None,
                discolor_score=None,
                scratch_score=None,
                surface_verdict=Verdict.NG,
                defect_codes=[],
                proc_time_ms=0,
            )

        # ④ 종합 판정 — combine 실패도 NG 폴백으로 보장.
        total = int(round((time.perf_counter() - t0) * 1000))
        try:
            result = combine_verdict(length, surface, master, proc_time_ms=total)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"verdict 실패: {type(exc).__name__}: {exc}")
            result = _error_verdict(master, proc_time_ms=total)

        # 단계 오류가 있었다면 신뢰할 수 없는 판정이므로 NG + 재확인 강제
        # (자동검사율 100% 유지하되 오검 방지 — 재확인 대상으로 보낸다).
        if errors:
            result = result.model_copy(
                update={
                    "final_verdict": Verdict.NG.value,
                    "review_flag": True,
                    "confidence": 0.0,
                }
            )

        timings = StageTimings(
            preprocess_ms=getattr(pre, "proc_time_ms", 0),
            length_ms=length.proc_time_ms,
            surface_ms=surface.proc_time_ms,
            verdict_ms=max(
                0,
                total
                - getattr(pre, "proc_time_ms", 0)
                - length.proc_time_ms
                - surface.proc_time_ms,
            ),
            total_ms=total,
        )
        reason = "; ".join(errors) if errors else None
        return result, timings, reason


def to_inspection_result(
    verdict: VerdictResult,
    *,
    lot: str,
    item_code: str,
    cam_id: str,
    inspected_at: Optional[datetime] = None,
    work_order: Optional[str] = None,
    shift: Optional[str] = None,
    operator: Optional[str] = None,
    raw_image_path: Optional[str] = None,
    result_image_path: Optional[str] = None,
) -> InspectionResult:
    """VerdictResult → InspectionResult (POST /inspection 본문).

    HTTP 전송은 backend 책임. 본 함수는 스키마 매핑만 수행한다.
    id/mes_synced 는 서버가 채운다.
    """
    length = verdict.length
    surface = verdict.surface
    return InspectionResult(
        lot=lot,
        work_order=work_order,
        item_code=item_code,
        cam_id=cam_id,
        inspected_at=inspected_at or datetime.now(timezone.utc),
        shift=shift,
        operator=operator,
        ref_length_mm=length.ref_length_mm,
        meas_length_mm=length.meas_length_mm,
        deviation_mm=length.deviation_mm,
        length_verdict=length.length_verdict,
        oil_score=surface.oil_score,
        discolor_score=surface.discolor_score,
        scratch_score=surface.scratch_score,
        final_verdict=verdict.final_verdict,
        defect_codes=list(verdict.defect_codes),
        confidence=verdict.confidence,
        raw_image_path=raw_image_path,
        result_image_path=result_image_path,
        proc_time_ms=verdict.proc_time_ms,
        review_flag=verdict.review_flag,
    )


__all__ = [
    "InspectionPipeline",
    "StageTimings",
    "to_inspection_result",
]


# combine_verdict 가 surface_verdict 를 직접 쓰지 않으므로(코드 합집합 기반),
# 표면 폴백에서 surface_verdict=NG/ defect_codes=[MULTI] 를 주면 NG 가 보장된다.
