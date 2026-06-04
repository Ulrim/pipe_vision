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
    InspectionResult,
    ItemMaster,
    Verdict,
    VerdictResult,
)

from .length import measure_length
from .preprocess import preprocess
from .surface import analyze_surface
from .surface.model import ClassicalSurfaceModel, SurfaceModel
from .verdict import combine_verdict


def _to_item(item: Union[ItemMaster, Dict[str, Any]]) -> ItemMaster:
    if isinstance(item, ItemMaster):
        return item
    return ItemMaster(**item)


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
        """프레임 1장 → VerdictResult. 결정적, 전체 proc_time_ms 계측."""
        t0 = time.perf_counter()
        master = _to_item(item)

        # ② 전처리
        pre = preprocess(frame_bgr, self.surface_inset_ratio)

        # ③ 길이 측정 (length_roi 기반)
        if pre.length_roi is not None:
            gray_roi = pre.length_roi.crop(pre.gray_corrected)
        else:
            gray_roi = pre.gray_corrected  # ROI 미검출 → 전체로 시도(끝단검출 실패 유도)
        length = measure_length(gray_roi, master)

        # ③ 표면 판정 (surface_roi 기반)
        if pre.surface_roi is not None:
            region = pre.surface_roi.crop(frame_bgr)
            region_mask = pre.surface_roi.crop(pre.mask)
        else:
            region = frame_bgr
            region_mask = pre.mask
        surface = self.surface_model.predict(region, master, mask=region_mask)

        # ④ 종합 판정
        elapsed = int(round((time.perf_counter() - t0) * 1000))
        result = combine_verdict(length, surface, master, proc_time_ms=elapsed)
        return result

    def run_with_timings(
        self,
        frame_bgr: np.ndarray,
        item: Union[ItemMaster, Dict[str, Any]],
    ) -> tuple[VerdictResult, StageTimings]:
        """run() 과 동일하나 단계별 타이밍 분해를 함께 반환."""
        t0 = time.perf_counter()
        master = _to_item(item)

        pre = preprocess(frame_bgr, self.surface_inset_ratio)
        if pre.length_roi is not None:
            gray_roi = pre.length_roi.crop(pre.gray_corrected)
        else:
            gray_roi = pre.gray_corrected
        length = measure_length(gray_roi, master)

        if pre.surface_roi is not None:
            region = pre.surface_roi.crop(frame_bgr)
            region_mask = pre.surface_roi.crop(pre.mask)
        else:
            region = frame_bgr
            region_mask = pre.mask
        surface = self.surface_model.predict(region, master, mask=region_mask)

        total = int(round((time.perf_counter() - t0) * 1000))
        result = combine_verdict(length, surface, master, proc_time_ms=total)
        timings = StageTimings(
            preprocess_ms=pre.proc_time_ms,
            length_ms=length.proc_time_ms,
            surface_ms=surface.proc_time_ms,
            verdict_ms=max(
                0,
                total
                - pre.proc_time_ms
                - length.proc_time_ms
                - surface.proc_time_ms,
            ),
            total_ms=total,
        )
        return result, timings


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
