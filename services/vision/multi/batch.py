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
from typing import Any, Dict, List, Optional, Union

import cv2
import numpy as np
from aivis_types import ItemMaster, Verdict, VerdictResult

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
    index: int, roi: TubeROI, vr: VerdictResult
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
        vr = pipe.run(crop, item)
        tubes.append(_tube_from_verdict(roi.index, roi, vr))

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


__all__ = ["TubeResult", "BatchResult", "inspect_batch"]
