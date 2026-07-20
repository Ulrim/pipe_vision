"""다중 튜브 배치 검사 (M3~M5 를 튜브별로 재사용).

segment_tubes 로 분할한 각 스트립에 단일 튜브 검사 파이프라인
(preprocess → measure_length → analyze_surface → combine_verdict)을 그대로
적용해 튜브별 길이+표면 판정을 낸다. 기존 단일 튜브 모듈을 재사용하므로
알고리즘 중복이 없다.

주의(오케스트레이터 승인 전): BatchResult/TubeResult 는 shared-types 스키마를
바꾸지 않고 services/vision 내부 dataclass 로만 둔다. InspectionResult 매핑은
후속 작업(승인 후)으로 남긴다.

결정성: segment_tubes + InspectionPipeline 모두 결정적 → 동일 입력 동일 출력.
proc_time_ms: 전체 배치 + 튜브당 평균을 계측한다(KPI 회귀 추적용).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import cv2
import numpy as np
from aivis_types import InspectionResult, ItemMaster, Verdict, VerdictResult

from ..length.measure import LengthSpan
from ..pipeline import InspectionPipeline
from .segment import TubeROI, segment_tubes


@dataclass
class TubeResult:
    """단일 튜브 검사 결과(내부 스키마). VerdictResult + ROI 위치."""

    index: int
    bbox: tuple  # (x0,y0,x1,y1)
    # 길이
    length_mm: Optional[float]
    deviation_mm: Optional[float]
    length_verdict: str
    # 표면 점수(0~1)
    oil_score: Optional[float]
    discolor_score: Optional[float]
    scratch_score: Optional[float]
    # 종합
    final_verdict: str
    defect_codes: List[str] = field(default_factory=list)
    confidence: Optional[float] = None
    review_flag: bool = False
    roi_confidence: float = 0.0
    proc_time_ms: int = 0
    # 길이 측정 스팬(끝단 2점·세로 범위) — **원본 프레임 좌표**로 환산된 값.
    # 배치 오버레이가 튜브별 측정선을 그리는 데 쓴다(끝단 미검출/세로축이면 None).
    length_span: Optional[LengthSpan] = None


@dataclass
class BatchResult:
    """다중 튜브 배치 검사 결과(내부 스키마)."""

    tubes: List[TubeResult]
    count_detected: int
    count_expected: Optional[int]
    count_ok: bool
    count_mismatch: bool
    batch_verdict: str  # 모든 튜브 OK & 개수 일치면 OK
    ng_count: int
    review_count: int
    axis: str
    proc_time_ms: int
    per_tube_avg_ms: float


def _v(x) -> str:
    """Verdict enum/문자열 → 문자열."""
    return x.value if isinstance(x, Verdict) else str(x)


def _tube_from_verdict(
    index: int,
    roi: TubeROI,
    vr: VerdictResult,
    length_span: Optional[LengthSpan] = None,
) -> TubeResult:
    length = vr.length
    surface = vr.surface
    codes = [c.value if hasattr(c, "value") else str(c) for c in vr.defect_codes]
    return TubeResult(
        index=index,
        bbox=roi.bbox,
        length_mm=length.meas_length_mm,
        deviation_mm=length.deviation_mm,
        length_verdict=_v(length.length_verdict),
        oil_score=surface.oil_score,
        discolor_score=surface.discolor_score,
        scratch_score=surface.scratch_score,
        final_verdict=_v(vr.final_verdict),
        defect_codes=codes,
        confidence=vr.confidence,
        review_flag=vr.review_flag,
        roi_confidence=roi.confidence,
        proc_time_ms=vr.proc_time_ms,
        length_span=length_span,
    )


def _tube_span_in_frame(
    crop_span: Optional[LengthSpan], roi: TubeROI, axis: str
) -> Optional[LengthSpan]:
    """튜브 crop 좌표계 span → 원본 프레임 좌표계 span 변환.

    horizontal 축은 crop 이 회전 없이 (roi.x0, roi.y0) 오프셋이므로 단순 평행이동.
    vertical 축은 crop 을 90° 회전해 측정하므로 좌표 매핑이 비자명 → 측정선
    오버레이는 생략한다(None). 개수/판정은 기존대로 정확하며, 시각화만 보류한다.
    """
    if crop_span is None or axis != "horizontal" or not crop_span.valid:
        return None
    return LengthSpan(
        left_x=crop_span.left_x + roi.x0,
        right_x=crop_span.right_x + roi.x0,
        y_top=crop_span.y_top + roi.y0,
        y_bottom=crop_span.y_bottom + roi.y0,
    )


def inspect_batch(
    frame: np.ndarray,
    item: Union[ItemMaster, Dict[str, Any]],
    *,
    axis: str = "horizontal",
    expected_count: Optional[int] = None,
    min_tubes: int = 1,
    max_tubes: int = 20,
    pipeline: Optional[InspectionPipeline] = None,
) -> BatchResult:
    """다중 튜브 프레임 → 튜브별 검사 + 배치 판정.

    각 스트립을 crop 해 단일 튜브 InspectionPipeline 으로 검사한다(길이는
    measure_length, 표면은 analyze_surface 재사용). axis="vertical"이면 스트립을
    90° 회전해 길이 측정이 가로 튜브 가정과 맞도록 한다.

    개수 확인: expected_count 가 주어지면 검출 N 과 비교해 count_ok/mismatch 설정.
    배치 판정: 모든 튜브 OK 이고 개수 불일치 없으면 OK, 아니면 NG.
    """
    t0 = time.perf_counter()
    pipe = pipeline or InspectionPipeline()

    # 개수 불일치를 감지하려면 expected 에 강제되지 않은 '독립' 자동 검출이
    # 필요하다(expected 로 강제 분할하면 count 가 항상 일치해 불일치를 못 잡음).
    auto_rois = segment_tubes(
        frame,
        axis=axis,
        expected_count=None,
        min_tubes=min_tubes,
        max_tubes=max_tubes,
    )
    count_detected = len(auto_rois)

    # 자동 개수가 기대와 같을 때만 expected 로 경계를 정밀 보정(더 안정적).
    if expected_count is not None and count_detected == expected_count:
        rois = segment_tubes(
            frame,
            axis=axis,
            expected_count=expected_count,
            min_tubes=min_tubes,
            max_tubes=max_tubes,
        )
    else:
        rois = auto_rois

    tubes: List[TubeResult] = []
    for roi in rois:
        crop = roi.crop(frame)
        if axis == "vertical":
            # 세로 튜브 → 가로로 회전(measure_length 는 가로 튜브 가정).
            crop = cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
        # 끝단 좌표(span)까지 받아 배치 오버레이 측정선 표기에 쓴다.
        vr, crop_span = pipe.run_with_geometry(crop, item)
        frame_span = _tube_span_in_frame(crop_span, roi, axis)
        tubes.append(_tube_from_verdict(roi.index, roi, vr, frame_span))

    count_mismatch = (
        expected_count is not None and count_detected != expected_count
    )
    count_ok = not count_mismatch

    ng_count = sum(1 for t in tubes if t.final_verdict == Verdict.NG.value)
    review_count = sum(1 for t in tubes if t.review_flag)

    all_ok = count_detected > 0 and ng_count == 0
    batch_verdict = (
        Verdict.OK.value if (all_ok and count_ok) else Verdict.NG.value
    )

    total_ms = int(round((time.perf_counter() - t0) * 1000))
    per_tube = round(total_ms / count_detected, 3) if count_detected else 0.0

    return BatchResult(
        tubes=tubes,
        count_detected=count_detected,
        count_expected=expected_count,
        count_ok=count_ok,
        count_mismatch=count_mismatch,
        batch_verdict=batch_verdict,
        ng_count=ng_count,
        review_count=review_count,
        axis=axis,
        proc_time_ms=total_ms,
        per_tube_avg_ms=per_tube,
    )


@dataclass
class BatchMeta:
    """배치 공통 메타 — 한 프레임(오더)의 모든 튜브 행이 공유하는 값.

    한 번의 배치 촬영에서 inspected_at/raw/result 이미지 경로는 고정이며, 각
    튜브 행은 tube_index 로만 구분된다(자연키: cam_id+inspected_at+item_code+tube_index).
    ref_length_mm 는 TubeResult 가 보유하지 않으므로(스트립 판정만 담음) 배치
    메타로 item.ref_length_mm 을 실어 나른다 — 단일 to_inspection_result 와 정합.
    """

    lot: str
    item_code: str
    cam_id: str
    inspected_at: datetime
    ref_length_mm: Optional[float] = None
    work_order: Optional[str] = None
    shift: Optional[str] = None
    operator: Optional[str] = None
    raw_image_path: Optional[str] = None
    result_image_path: Optional[str] = None


def tube_to_inspection(
    tube: TubeResult,
    *,
    batch_meta: BatchMeta,
) -> InspectionResult:
    """단일 TubeResult → InspectionResult (POST /inspection 본문 1건).

    단일 튜브 to_inspection_result 와 필드 매핑을 일치시킨다. 차이는 두 가지:
      - tube_index = tube.index - 1 (roi.index 1..N → 0-base 0..N-1, 자연키 구성).
      - 이미지 경로/검사시각은 배치 공유값(batch_meta)에서 가져온다(1회 저장).
    length/verdict/score/proc_time_ms 등은 튜브별 값을 그대로 싣는다.
    id/mes_synced 는 서버가 채운다.
    """
    return InspectionResult(
        lot=batch_meta.lot,
        work_order=batch_meta.work_order,
        item_code=batch_meta.item_code,
        cam_id=batch_meta.cam_id,
        inspected_at=batch_meta.inspected_at,
        tube_index=max(0, int(tube.index) - 1),
        shift=batch_meta.shift,
        operator=batch_meta.operator,
        ref_length_mm=batch_meta.ref_length_mm,
        meas_length_mm=tube.length_mm,
        deviation_mm=tube.deviation_mm,
        length_verdict=tube.length_verdict or None,
        oil_score=tube.oil_score,
        discolor_score=tube.discolor_score,
        scratch_score=tube.scratch_score,
        final_verdict=tube.final_verdict,
        defect_codes=list(tube.defect_codes),
        confidence=tube.confidence,
        raw_image_path=batch_meta.raw_image_path,
        result_image_path=batch_meta.result_image_path,
        proc_time_ms=tube.proc_time_ms,
        review_flag=tube.review_flag,
    )


__all__ = [
    "TubeResult",
    "BatchResult",
    "BatchMeta",
    "inspect_batch",
    "tube_to_inspection",
]
