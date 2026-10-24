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
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
from aivis_types import InspectionResult, ItemMaster, Verdict, VerdictResult

from ..length.measure import END_MARGIN_PX, LengthSpan
from ..pipeline import CropContext, FramePrep, InspectionPipeline
from ..preprocess.area import InspectArea, area_rect
from ..preprocess.foreground import angle_gap, contrast_mask, dominant_angle
from .dense import axis_angle, find_dense_bundle, ribbon_crop
from .layout import Layout, find_layout, layout_from_bundle
from .segment import MAX_TUBES_HARD, TubeROI, segment_tubes

#: 튜브가 축(가로·세로)에서 이만큼 넘게 기울면 프레임을 돌려 바로 세운다(axis="auto" 일 때).
#: 그 밑의 기울기는 길이 측정의 기울기 보정이 처리한다.
DESKEW_MIN_DEG = 8.0

#: 기대개수가 없을 때 **자동** 개수추정에 쓰는 보수적 상한.
#: 하드 상한(MAX_TUBES_HARD)과 다르다 — 자동 추정은 상한이 크면 최소 피치가
#: 작아져 없는 seam 까지 찾아 과분할한다. 기대개수를 주면 그만큼 올라간다.
DEFAULT_AUTO_MAX_TUBES = 20


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
    # 프레임을 돌려 바로 세운 뒤 잰 튜브 — bbox 는 돌린 영상 좌표라 원본 사진에 맞지 않는다.
    rotated: bool = False
    # 제품 온도(°C). 있으면 length_mm·deviation_mm 은 20°C 환산값(calib/temperature.py).
    meas_temp_c: Optional[float] = None
    # 붙은 다발(dense.py)에서 잰 튜브: 네 모서리와 측정선(결과 사진 좌표). 비스듬·원근이라 상자로는 못 그린다.
    quad: Optional[List[Tuple[float, float]]] = None
    axis_line: Optional[Tuple[Tuple[float, float], Tuple[float, float]]] = None


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
    #: 비스듬한 튜브를 바로 세우려고 돌린 각도(도)와 그 영상(결과 사진은 이 영상에 그린다).
    rotation_deg: float = 0.0
    view: Optional[np.ndarray] = None
    #: 붙은 다발 경로로 셌다(dense.py) / 그 개수를 믿기 어렵다(경계가 애매 — 튜브 전부 재확인).
    dense: bool = False
    count_uncertain: bool = False
    notes: List[str] = field(default_factory=list)


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
        meas_temp_c=length.meas_temp_c,
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


def tube_window(roi: TubeROI, frame_shape, *, axis: str = "horizontal",
                bounds=None, pad: Optional[int] = None) -> TubeROI:
    """튜브를 잴 crop — 분할 상자를 **축 방향으로** 2×END_MARGIN_PX 넓힌다.

    분할 상자는 다발 전체의 끝단에서 딱 끝나므로 그대로 자르면 가장 긴 튜브의 끝단 바깥
    배경이 없다 — 밝기 변화가 crop 밖이라 서브픽셀로 잴 것이 없고, 창 경계를 끝단으로 잡아
    '상자 폭 −1px' 로 고정됐다(2026-10-17). bounds(검사 영역 픽셀 상자) 밖으로는 넓히지
    않는다. 축과 직각으로는 넓히지 않는다(이웃 튜브가 들어온다).
    """
    fh, fw = frame_shape[:2]
    bx0, by0, bx1, by1 = ((bounds.x0, bounds.y0, bounds.x1, bounds.y1) if bounds is not None
                          else (0, 0, fw, fh))
    pad = 2 * END_MARGIN_PX if pad is None else int(pad)
    if axis == "vertical":
        return TubeROI(index=roi.index, x0=roi.x0, x1=roi.x1,
                       y0=max(by0, roi.y0 - pad), y1=min(by1, roi.y1 + pad),
                       confidence=roi.confidence)
    return TubeROI(index=roi.index, y0=roi.y0, y1=roi.y1,
                   x0=max(bx0, roi.x0 - pad), x1=min(bx1, roi.x1 + pad),
                   confidence=roi.confidence)


def inspect_batch(
    frame: np.ndarray,
    item: Union[ItemMaster, Dict[str, Any]],
    *,
    axis: str = "horizontal",
    expected_count: Optional[int] = None,
    min_tubes: int = 1,
    max_tubes: Optional[int] = None,
    pipeline: Optional[InspectionPipeline] = None,
    stage: Optional[str] = None,
    area: Optional[InspectArea] = None,
) -> BatchResult:
    """다중 튜브 프레임 → 튜브별 검사 + 배치 판정.

    각 스트립을 crop 해 단일 튜브 InspectionPipeline 으로 검사한다(길이는
    measure_length, 표면은 analyze_surface 재사용). axis="vertical"이면 스트립을
    90° 회전해 길이 측정이 가로 튜브 가정과 맞도록 한다.

    개수 확인: expected_count 가 주어지면 검출 N 과 비교해 count_ok/mismatch 설정.
    배치 판정: 모든 튜브 OK 이고 개수 불일치 없으면 OK, 아니면 NG.

    `max_tubes` 는 **expected_count 보다 작게 내려가지 않는다.** 예전에는 20 이
    하드코딩돼 있어서 expected_count=64 를 줘도 자동 검출이 20 에서 잘렸고,
    그러면 기대와 불일치해 20개짜리 결과로 떨어졌다 — 튜브 44개가 예외도 경고도
    없이 사라졌다. 다발 동시 절단이 상시 공정이므로(부록 A.1) 이 경로가 조용히
    잘리면 수량이 틀린 채로 검사 결과가 쌓인다.

    `area`(검사 영역)를 주면 그 안에서만 튜브를 나눈다(segment_tubes). 튜브 bbox·측정선은
    원본 프레임 좌표 그대로다.

    **렌즈 왜곡 보정·기준자는 전체 프레임에서 한 번**(2026-10-17, pipe.prepare_frame).
    종전엔 튜브 crop 마다 단일 파이프라인이 다시 했다 — 렌즈 보정은 crop 을 센서 전체로
    보고 엉뚱하게 휘었고, 기준자는 프레임 좌표 ROI 를 crop 에서 찾다 '읽기 실패' 로 튜브가
    전부 NG + 재확인이 됐다. 이제 보정된 프레임에서 나누고 자르며, 튜브마다 기준자 좌표
    원점·방향(CropContext)을 넘겨 끝단 옆 마크로 자를 읽는다. 렌즈 보정을 켜면 튜브 bbox·
    측정선은 보정된 좌표다(단일 경로와 같다 — 왜곡 이동량은 가장자리 몇 px).
    """
    t0 = time.perf_counter()
    pipe = pipeline or InspectionPipeline()
    prep = pipe.prepare_frame(frame, axis="vertical" if axis == "vertical" else "horizontal",
                              stage=stage, item=item)
    frame = prep.frame
    rotation = 0.0
    view: Optional[np.ndarray] = None
    if axis == "auto":
        axis, frame, prep, area, rotation = _orient(frame, prep, area)
        view = frame if rotation else None
        # 방향을 영상에서 정할 때는 배치 찾기(layout.py)로 센다 — 덩어리 먼저, 붙은 덩어리만 나누고
        # 튜브마다 중심선을 따라 편 띠에서 잰다(어두운·밝은 배경 공통, 기울기·틈·기준자 대응).
        return _inspect_layout(frame, prep=prep, pipe=pipe, item=item, stage=stage, axis=axis, area=area,
                               rotation=rotation, view=view, expected_count=expected_count, t0=t0)

    # 자동 개수 추정은 상한이 커질수록 불안정하다. 상한이 크면 최소 피치가
    # 작아져 없는 seam 까지 찾아내 과분할한다(6개 프레임을 7개로 셌다).
    # 그래서 **자동 추정 상한과 하드 상한을 분리**한다 — 기본은 보수적으로
    # 두고, 기대개수가 주어지면 거기까지만 올린다.
    cap = DEFAULT_AUTO_MAX_TUBES if max_tubes is None else int(max_tubes)
    if expected_count:
        cap = max(cap, int(expected_count))
    max_tubes = max(1, min(MAX_TUBES_HARD, cap))

    # 개수 불일치를 감지하려면 expected 에 강제되지 않은 '독립' 자동 검출이
    # 필요하다(expected 로 강제 분할하면 count 가 항상 일치해 불일치를 못 잡음).
    auto_rois = segment_tubes(
        frame,
        axis=axis,
        expected_count=None,
        min_tubes=min_tubes,
        max_tubes=max_tubes,
        area=area,
        valid=prep.valid,
    )
    count_detected = len(auto_rois)

    # 밝은 배경에서 튜브가 붙어 누우면(트레이에 다발로) 위 분할은 덩어리를 못 나눈다(현장 사진: 19개 중
    # 0~5개). 줄무늬로 본 다발 분석(dense.py)이 더 많이 찾으면 그쪽으로 잰다. 떨어져 놓인 튜브는 위
    # 분할이 그대로 맞다(다발 분석은 이어진 덩어리만 세므로 개수가 같거나 적다).
    if prep.light_bg:
        found = _dense_bundle(frame, axis, area, valid=prep.valid)
        if found is not None and found[0].count > count_detected:
            return _inspect_dense(found, frame=frame, axis=axis, rotation=rotation, view=view,
                                  prep=prep, pipe=pipe, item=item, stage=stage,
                                  expected_count=expected_count, t0=t0)

    # 자동 개수가 기대와 같을 때만 expected 로 경계를 정밀 보정(더 안정적).
    if expected_count is not None and count_detected == expected_count:
        rois = segment_tubes(
            frame,
            axis=axis,
            expected_count=expected_count,
            min_tubes=min_tubes,
            max_tubes=max_tubes,
            area=area,
            valid=prep.valid,
        )
    else:
        rois = auto_rois

    # 튜브 crop 은 분할 상자보다 축 방향으로 넓게(tube_window) — 끝단 바깥 배경이 있어야
    # 서브픽셀로 잰다. 화면·검사 영역 밖으로는 넓히지 않는다(밖은 검사하지 않는다).
    bound = area_rect(area, frame)
    # 밝은 배경: 제품 마스크를 전체 프레임에서 한 번 찾아 튜브마다 잘라 넘긴다(crop 에서 다시 찾으면
    # 튜브가 화면을 채워 틀린다). 끝단 바깥 배경도 더 넉넉히 담는다(배경 마스크는 덜 정확하다).
    full_mask = contrast_mask(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)) if prep.light_bg else None
    if full_mask is not None and prep.valid is not None:
        full_mask[prep.valid == 0] = 0
    tubes: List[TubeResult] = []
    for roi in rois:
        pad = None
        if full_mask is not None:
            along = (roi.y1 - roi.y0) if axis == "vertical" else (roi.x1 - roi.x0)
            pad = max(4 * END_MARGIN_PX, int(0.04 * along))
        win = tube_window(roi, frame.shape, axis=axis, bounds=bound, pad=pad)
        crop = win.crop(frame)
        mask_c = win.crop(full_mask) if full_mask is not None else None
        if axis == "vertical":
            # 세로 튜브 → 가로로 회전(measure_length 는 가로 튜브 가정).
            crop = cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
            if mask_c is not None:
                mask_c = cv2.rotate(mask_c, cv2.ROTATE_90_CLOCKWISE)
        # 끝단 좌표(span)까지 받아 배치 오버레이 측정선 표기에 쓴다.
        # 검사 단계(=모드)를 튜브마다 그대로 넘긴다 — 길이 모드면 길이만 판정.
        if axis == "vertical":
            # 시계방향 회전: crop x' = (높이-1) - y_local → 프레임 y = y0 + 높이-1 - x'.
            ctx = CropContext(prep=prep, origin=float(win.y1 - 1), direction=-1, mask=mask_c)
        else:
            ctx = CropContext(prep=prep, origin=float(win.x0), direction=1, mask=mask_c)
        vr, crop_span = pipe.run_with_geometry(crop, item, stage=stage, crop=ctx)
        # 측정선은 실제로 자른 창(win) 기준 좌표 → 프레임. bbox 는 분할 상자 그대로.
        frame_span = _tube_span_in_frame(crop_span, win, axis)
        tube = _tube_from_verdict(roi.index, roi, vr, frame_span)
        tube.rotated = bool(rotation)
        tubes.append(tube)

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
        rotation_deg=round(rotation, 1),
        view=view,
    )


def _dense_bundle(frame: np.ndarray, axis: str, area: Optional[InspectArea], *,
                  valid: Optional[np.ndarray] = None):
    """붙은 다발 찾기 — (DenseBundle, 정준 영상, 정준→결과사진 좌표 함수) 또는 None.

    정준 영상 = 튜브가 가로(세로 축이면 시계방향으로 돌린 것). 검사 영역이 있으면 그 안만 본다.
    """
    rect = area_rect(area, frame)
    base = rect.crop(frame) if rect is not None else frame
    ox, oy = (rect.x0, rect.y0) if rect is not None else (0, 0)
    vertical = axis == "vertical"
    canon = cv2.rotate(base, cv2.ROTATE_90_CLOCKWISE) if vertical else base
    vmask = None
    if valid is not None:
        vmask = rect.crop(valid) if rect is not None else valid
        vmask = cv2.rotate(vmask, cv2.ROTATE_90_CLOCKWISE) if vertical else vmask
    bundle = find_dense_bundle(cv2.cvtColor(canon, cv2.COLOR_BGR2GRAY), valid=vmask)
    if bundle is None:
        return None
    hb = base.shape[0]
    if vertical:
        # 시계방향 회전: 정준 (x', y') = (H − 1 − y, x) → 원래 (x, y) = (y', H − 1 − x').
        def to_view(x: float, y: float) -> Tuple[float, float]:
            return float(y + ox), float(hb - 1 - x + oy)
    else:
        def to_view(x: float, y: float) -> Tuple[float, float]:
            return float(x + ox), float(y + oy)
    return bundle, canon, to_view


def _inspect_dense(found, *, frame: np.ndarray, axis: str, rotation: float, view: Optional[np.ndarray],
                   prep: FramePrep, pipe: InspectionPipeline, item, stage: Optional[str],
                   expected_count: Optional[int], t0: float) -> BatchResult:
    """붙은 다발: 튜브마다 중심선을 따라 편 띠(ribbon)를 단일 파이프라인으로 잰다.

    띠는 회전만 한 1:1 영상이라 띠에서 잰 px 길이가 원본 px 길이다. 끝은 다발 분석이 준 위치를
    출발점(제품 마스크)으로 파이프라인이 서브픽셀로 다시 찾는다(밝은 배경 경로 — 50% 교차).
    개수가 애매하면(DenseBundle.uncertain) 튜브 전부 재확인 + 배치 NG — 조용히 고르지 않는다.
    """
    bundle, canon, to_view = found
    notes = list(bundle.notes)
    dprep = prep
    if prep.gauge is not None or prep.gauge_read:
        msg = "붙은 다발은 튜브를 띠로 펴서 재므로 기준자를 쓰지 않았다(저장된 배율) — 정렬된 치구에서 쓰세요"
        notes.append(msg)
        dprep = FramePrep(frame=prep.frame, lens_applied=prep.lens_applied, gauge=None,
                          gauge_read=prep.gauge_read, notes=tuple(list(prep.notes) + [msg]),
                          light_bg=prep.light_bg)
    uncertain = bundle.uncertain
    tubes: List[TubeResult] = []
    for bt in bundle.tubes:
        slope = bundle.slope(0.5 * (bt.u0 + bt.u1))
        est = (bt.x1 - bt.x0) * float(np.hypot(1.0, slope))
        margin = max(4 * END_MARGIN_PX, int(0.08 * est))
        crop, mask, _length, cen, d = bundle.ribbon(canon, bt, margin=margin)
        ctx = CropContext(prep=dprep, origin=0.0, direction=1, mask=mask)
        vr, crop_span = pipe.run_with_geometry(crop, item, stage=stage, crop=ctx)
        quad = [to_view(x, y) for x, y in bundle.quad(bt)]
        xs, ys = [q[0] for q in quad], [q[1] for q in quad]
        roi = TubeROI(index=bt.index, x0=int(np.floor(min(xs))), y0=int(np.floor(min(ys))),
                      x1=int(np.ceil(max(xs))), y1=int(np.ceil(max(ys))),
                      confidence=0.5 if uncertain else 1.0)
        tube = _tube_from_verdict(bt.index, roi, vr, None)
        tube.quad = quad
        if crop_span is not None and crop_span.valid:
            half = 0.5 * crop.shape[1]
            pa = cen + (crop_span.left_x - half) * d
            pb = cen + (crop_span.right_x - half) * d
            tube.axis_line = (to_view(*pa), to_view(*pb))
        # 비스듬하면 bbox 가 이웃을 품는다 — 라벨링용 위치(tube_bbox)는 비운다.
        tube.rotated = bool(rotation) or abs(slope) > 0.02
        if uncertain:
            tube.review_flag = True
        tubes.append(tube)

    count_detected = len(tubes)
    count_mismatch = expected_count is not None and count_detected != expected_count
    count_ok = not count_mismatch
    ng_count = sum(1 for t in tubes if t.final_verdict == Verdict.NG.value)
    review_count = sum(1 for t in tubes if t.review_flag)
    all_ok = count_detected > 0 and ng_count == 0
    batch_verdict = Verdict.OK.value if (all_ok and count_ok and not uncertain) else Verdict.NG.value
    total_ms = int(round((time.perf_counter() - t0) * 1000))
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
        per_tube_avg_ms=round(total_ms / count_detected, 3) if count_detected else 0.0,
        rotation_deg=round(rotation, 1),
        view=view,
        dense=True,
        count_uncertain=uncertain,
        notes=notes,
    )


def _canonical(frame: np.ndarray, axis: str, area: Optional[InspectArea], valid: Optional[np.ndarray]):
    """정준 영상(튜브가 가로 — 세로 축이면 시계방향으로 돌림)과 좌표 변환.

    반환: (정준 BGR, 정준 valid, 정준→결과사진 좌표 함수, 정준 x → 기준자 좌표 (원점, 방향)).
    기준자 좌표 = 가로 축이면 프레임 x, 세로 축이면 프레임 y(파이프라인 CropContext 규약).
    """
    rect = area_rect(area, frame)
    base = rect.crop(frame) if rect is not None else frame
    ox, oy = (rect.x0, rect.y0) if rect is not None else (0, 0)
    vertical = axis == "vertical"
    canon = cv2.rotate(base, cv2.ROTATE_90_CLOCKWISE) if vertical else base
    vmask = None
    if valid is not None:
        vmask = rect.crop(valid) if rect is not None else valid
        vmask = cv2.rotate(vmask, cv2.ROTATE_90_CLOCKWISE) if vertical else vmask
    hb = base.shape[0]
    if vertical:
        # 시계방향 회전: 정준 (x', y') = (H − 1 − y, x) → 원래 (x, y) = (y', H − 1 − x').
        def to_view(x: float, y: float) -> Tuple[float, float]:
            return float(y + ox), float(hb - 1 - x + oy)

        def gauge_axis(x: float) -> Tuple[float, int]:
            return float(hb - 1 - x + oy), -1
    else:
        def to_view(x: float, y: float) -> Tuple[float, float]:
            return float(x + ox), float(y + oy)

        def gauge_axis(x: float) -> Tuple[float, int]:
            return float(x + ox), 1
    return canon, vmask, to_view, gauge_axis


def _separated_ok(lay: Layout) -> bool:
    """덩어리 하나 = 튜브 하나로 2개 이상, 확실하고, 길이가 서로 30% 안 — 바닥 위에 떨어져 놓인 튜브."""
    if lay.uncertain or lay.count < 2 or any(t.source != "blob" for t in lay.tubes):
        return False
    lens = [t.length for t in lay.tubes]
    return max(lens) <= 1.3 * min(lens)


def _unit_px(item) -> Optional[float]:
    """기준정보 외경 ÷ 배율 = 튜브 한 개 폭(px). 없으면 None(덩어리들에서 추정)."""
    get = item.get if isinstance(item, dict) else (lambda k, d=None: getattr(item, k, d))
    od, sc = get("outer_diameter_mm", None), get("px_to_mm_scale", None)
    try:
        od, sc = float(od), float(sc)
    except (TypeError, ValueError):
        return None
    return od / sc if od > 0 and sc > 0 else None


def _inspect_layout(frame: np.ndarray, *, prep: FramePrep, pipe: InspectionPipeline, item, stage: Optional[str],
                    axis: str, area: Optional[InspectArea], rotation: float, view: Optional[np.ndarray],
                    expected_count: Optional[int], t0: float) -> BatchResult:
    """axis="auto" 경로 — 배치 찾기(layout.py) → 튜브마다 중심선을 따라 편 띠(ribbon)를 단일 파이프라인으로.

    띠는 회전만 한 1:1 영상이라 띠에서 잰 px 길이가 원본 px 길이다. 기준자가 이 프레임에서 읽혔고
    영상을 돌리지 않았으면 띠 x 를 기준자 축에 1:1 로 대응시켜 국소 배율로 잰다(비스듬해도 실제 길이 —
    배율은 방향과 무관하다). 개수가 애매하면 튜브 전부 재확인 + 배치 NG — 조용히 고르지 않는다.
    밝은 배경에서 붙은 큰 다발은 제품 마스크에 안 잡혀(배경으로 보임) 화면 전체 줄무늬 분석이 더 많이
    찾으면 그쪽을 쓴다.
    """
    canon, vmask, to_view, gauge_axis = _canonical(frame, axis, area, prep.valid)
    gray = cv2.cvtColor(canon, cv2.COLOR_BGR2GRAY)
    unit = _unit_px(item)
    lay: Layout = find_layout(gray, light=prep.light_bg, valid=vmask, unit_px=unit)
    if prep.light_bg:
        # 밝은 배경: 붙은 큰 다발은 마스크에 안 잡히거나 트레이 테두리째 잡힌다 — 화면 전체 줄무늬 분석이
        # 더 많이 찾거나, 덩어리 쪽이 불확실한데 줄무늬 쪽은 확실하면 줄무늬로.
        # 단, 덩어리 쪽이 튜브 폭을 알고 있으면 줄무늬 간격이 그 폭과 맞아야 한다 — 떨어진 튜브 몇 개가 놓인
        # 바닥 전체를 무늬로 잘게 썰어 수십~수백 개로 세는 일을 막는다(직접 만든 바닥 장면에서 155개).
        # 떨어진 튜브가 덩어리마다 하나씩, 폭·길이가 고르게 잡혔으면(바닥 위 낱개) 줄무늬 분석은 보지 않는다.
        bundle = None if _separated_ok(lay) else find_dense_bundle(gray, valid=vmask, pitch_hint=unit)
        if bundle is not None and (bundle.count > lay.count or (lay.uncertain and not bundle.uncertain)):
            cell = float(np.median([t.u1 - t.u0 for t in bundle.tubes]))
            if lay.unit_px is None or lay.count < 2 or 0.6 <= cell / lay.unit_px <= 1.6:
                lay = layout_from_bundle(bundle)
    notes = list(lay.notes)
    if lay.rulers:
        notes.append(f"기준자(눈금 띠) {lay.rulers}개는 개수에서 뺐다")
    # 기준자는 가로 축으로 읽었다(auto 는 prepare_frame 에 horizontal 을 준다) — 세로로 판단되면 쓰지 않는다.
    # 돌린 영상이면 _orient 가 이미 gauge 를 비웠다.
    dprep = prep
    if prep.gauge is not None and axis != "horizontal":
        msg = "튜브가 세로로 놓여 가로 기준자를 쓰지 않았다(저장된 배율) — 기준자를 튜브와 나란히 두세요"
        notes.append(msg)
        dprep = FramePrep(frame=prep.frame, lens_applied=prep.lens_applied, gauge=None,
                          gauge_read=prep.gauge_read, notes=tuple(list(prep.notes) + [msg]),
                          light_bg=prep.light_bg, valid=prep.valid)
    uncertain = lay.uncertain
    tubes: List[TubeResult] = []
    for i, g in enumerate(lay.tubes):
        margin = max(4 * END_MARGIN_PX, int(0.08 * g.length))
        crop, mask, _length, cen, d = ribbon_crop(canon, g.p0, g.p1, 0.6 * g.width, margin=margin)
        # 띠 x = 0 인 점의 기준자 좌표 — 띠 화소 1px = 기준자 축 1px(배율은 방향과 무관). 기준자가 없으면 안 쓰인다.
        origin, direction = gauge_axis(float(cen[0] - 0.5 * crop.shape[1] * d[0]))
        # 제품 마스크(배치 찾기가 준 끝 = 출발점)를 넘긴다 — 띠는 튜브가 화면 높이를 다 채워 띠 안에서 다시
        # 찾으면 위아래 배경이 없어 끝단을 놓쳤다(어두운 바닥 장면에서 튜브 9개 중 4개). 정밀한 끝은 파이프라인이
        # 출발점 양옆(END_MARGIN_PX)에서 서브픽셀로 다시 잰다.
        ctx = CropContext(prep=dprep, origin=origin, direction=direction, mask=mask)
        vr, crop_span = pipe.run_with_geometry(crop, item, stage=stage, crop=ctx)
        quad = [to_view(float(x), float(y)) for x, y in g.quad()]
        xs, ys = [q[0] for q in quad], [q[1] for q in quad]
        roi = TubeROI(index=i + 1, x0=int(np.floor(min(xs))), y0=int(np.floor(min(ys))),
                      x1=int(np.ceil(max(xs))), y1=int(np.ceil(max(ys))),
                      confidence=0.5 if uncertain else 1.0)
        tube = _tube_from_verdict(i + 1, roi, vr, None)
        tube.quad = quad
        if crop_span is not None and crop_span.valid:
            half = 0.5 * crop.shape[1]
            pa = cen + (crop_span.left_x - half) * d
            pb = cen + (crop_span.right_x - half) * d
            tube.axis_line = (to_view(*pa), to_view(*pb))
        # 비스듬하면 bbox 가 이웃을 품는다 — 라벨링용 위치(tube_bbox)는 비운다.
        tube.rotated = bool(rotation) or abs(g.slope) > 0.02
        if uncertain:
            tube.review_flag = True
        tubes.append(tube)

    count_detected = len(tubes)
    count_mismatch = expected_count is not None and count_detected != expected_count
    count_ok = not count_mismatch
    ng_count = sum(1 for t in tubes if t.final_verdict == Verdict.NG.value)
    review_count = sum(1 for t in tubes if t.review_flag)
    all_ok = count_detected > 0 and ng_count == 0
    batch_verdict = Verdict.OK.value if (all_ok and count_ok and not uncertain) else Verdict.NG.value
    total_ms = int(round((time.perf_counter() - t0) * 1000))
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
        per_tube_avg_ms=round(total_ms / count_detected, 3) if count_detected else 0.0,
        rotation_deg=round(rotation, 1),
        view=view,
        dense=lay.dense,
        count_uncertain=uncertain,
        notes=notes,
    )


def _rotate_keep(img: np.ndarray, deg: float, *, with_valid: bool = False):
    """영상을 deg 만큼 돌린다(잘리지 않게 캔버스를 넓힌다).

    넓어진 빈 모서리는 사진의 중앙값 색으로 채운다 — 가장자리 화소를 늘여 붙이면 줄무늬가 생겨
    '배경과 다른 곳' 으로 잡혔다(직접 만든 장면 평가에서 튜브 상자가 화면 끝까지 늘어남, 2026-10-24).
    with_valid=True 면 원래 사진이 있는 곳(255) 마스크도 돌려준다(가장자리 몇 px 안쪽으로 줄여서).
    """
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), deg, 1.0)
    c, s = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * s + w * c + 0.5), int(h * c + w * s + 0.5)
    m[0, 2] += nw / 2.0 - w / 2.0
    m[1, 2] += nh / 2.0 - h / 2.0
    fill = tuple(float(v) for v in np.median(img.reshape(-1, img.shape[2] if img.ndim == 3 else 1), axis=0))
    out = cv2.warpAffine(img, m, (nw, nh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                         borderValue=fill if img.ndim == 3 else fill[0])
    if not with_valid:
        return out
    valid = cv2.warpAffine(np.full((h, w), 255, np.uint8), m, (nw, nh), flags=cv2.INTER_NEAREST,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    k = max(3, int(0.01 * max(nw, nh)) | 1)
    valid = cv2.erode(valid, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    return out, valid


def _orient(frame: np.ndarray, prep: FramePrep, area: Optional[InspectArea]):
    """axis="auto": 튜브 방향을 영상에서 정하고, 비스듬하면(≥ DESKEW_MIN_DEG) 바로 세운다.

    현장 사진처럼 튜브가 30~40° 기울면 가로·세로 어느 축으로도 밴드가 잡히지 않는다. 대표 기울기만큼
    돌려 가로(또는 세로)로 만든 뒤 잰다. 검사 영역이 있으면 그 안만 돌린다(돌린 뒤에는 영역 = 화면).
    기준자는 원래 좌표에 있어 돌린 영상에 쓸 수 없다 — 설정돼 있으면 사유를 남긴다(NG + 재확인, 정렬 필요).
    반환: (axis, frame, prep, area, rotation_deg)
    """
    rect = area_rect(area, frame)
    base = rect.crop(frame) if rect is not None else frame
    gray_b = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY)
    ang = dominant_angle(gray_b)
    if prep.light_bg:
        # 밝은 배경: 붙은 다발은 덩어리를 못 나눠 덩어리 방향이 트레이 테두리를 따라간다(현장 사진에서
        # 14°·50° 처럼 틀렸다). 모서리 방향 분포(튜브 경계가 압도적)와 8° 넘게 다르면 그쪽을 쓴다.
        ga = axis_angle(gray_b)
        if ga is not None and (ang is None or angle_gap(ang, ga) > 8.0):
            ang = ga
    if ang is None:
        return "horizontal", frame, prep, area, 0.0
    axis = "vertical" if abs(ang) > 45.0 else "horizontal"
    target = 0.0 if axis == "horizontal" else (90.0 if ang > 0 else -90.0)
    dev = ang - target
    if abs(dev) < DESKEW_MIN_DEG:
        return axis, frame, prep, area, 0.0
    turned, valid = _rotate_keep(base, dev, with_valid=True)
    notes = list(prep.notes)
    if prep.gauge_read:
        notes.append(f"튜브가 {dev:+.0f}° 기울어 영상을 돌려 쟀다 — 기준자를 쓸 수 없다(카메라·치구 정렬 필요)")
    # 배경 밝기는 돌리기 전 사진에서 정한 값 그대로 — 돌린 영상의 빈 모서리(중앙값 색)가 테두리를 바꾼다.
    new_prep = FramePrep(frame=turned, lens_applied=prep.lens_applied, gauge=None,
                         gauge_read=prep.gauge_read, notes=tuple(notes), light_bg=prep.light_bg, valid=valid)
    return axis, turned, new_prep, None, dev


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
    #: 검사 단계(CUT_LENGTH|POST_WASH_SURFACE) — 한 프레임의 모든 튜브가 공유한다.
    inspection_stage: Optional[str] = None
    ref_length_mm: Optional[float] = None
    work_order: Optional[str] = None
    shift: Optional[str] = None
    operator: Optional[str] = None
    raw_image_path: Optional[str] = None
    result_image_path: Optional[str] = None


def _bbox_list(bbox) -> Optional[list]:
    """(x0,y0,x1,y1) → [int,...]. 형식이 이상하면 None(저장을 막지 않는다)."""
    try:
        vals = [int(round(float(v))) for v in bbox]
    except (TypeError, ValueError):
        return None
    if len(vals) != 4 or vals[2] <= vals[0] or vals[3] <= vals[1]:
        return None
    return vals


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
        inspection_stage=batch_meta.inspection_stage,
        inspected_at=batch_meta.inspected_at,
        tube_index=max(0, int(tube.index) - 1),
        # 사진 속 위치 — 라벨링 화면 강조·튜브 단위 학습셋 자르기(2026-10-11).
        tube_bbox=None if getattr(tube, "rotated", False) else _bbox_list(tube.bbox),
        shift=batch_meta.shift,
        operator=batch_meta.operator,
        ref_length_mm=batch_meta.ref_length_mm,
        meas_length_mm=tube.length_mm,
        deviation_mm=tube.deviation_mm,
        meas_temp_c=tube.meas_temp_c,
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
