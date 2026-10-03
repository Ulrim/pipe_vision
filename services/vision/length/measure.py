"""서브픽셀 길이 측정 (M3, §6.2).

알고리즘(결정적):
1) 전처리 ROI(length_roi) 내 그레이를 세로방향으로 평균 → 1D 수평 프로파일.
   파이프는 밝고 배경은 어두우므로 좌/우 끝단에서 급격한 그래디언트가 생긴다.
2) 프로파일의 그래디언트(소벨/차분)에서 좌측 최대(상승 에지), 우측 최소(하강 에지)
   위치를 픽셀 단위로 찾는다.
3) 그래디언트 극값 주변 3점 포물선 피팅으로 서브픽셀 끝단 좌표를 구한다.
4) **행별 서브픽셀 에지에 직선을 적합**해 기울기를 구하고, 두 직선의
   **수직거리**를 pixel_distance 로 쓴다(edges.fit_edge_lines).
   세로 평균의 수평거리를 그냥 쓰면 기울기 θ 에 대해 1/cosθ 배로 과대측정된다 —
   250mm 제품이 2° 기울면 +0.152mm 로, ±0.1mm 공차를 혼자 넘긴다.
   적합 실패 시에는 종전의 수평거리로 떨어진다(측정을 멈추지는 않는다).
5) length_mm = pixel_distance × scale.
   scale 은 **프레임마다 측정한 값(mm_per_px_override)** 이 우선이고, 없으면
   item.px_to_mm_scale(저장된 상수). 기준자를 같은 평면에 함께 찍으면 제품
   높이가 흔들려도 배율 오차가 상쇄된다 — calib/fiducial.py 참조.
6) deviation = meas - ref; OK = -tol_minus <= deviation <= +tol_plus.

끝단 검출 실패(프로파일 평탄/대비 부족) → edge_detected=False, NG.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from aivis_types import ItemMaster, LengthResult, Verdict

from ..calib.fiducial import FiducialScale
from .edges import EdgeLineFit, fit_edge_lines


@dataclass(frozen=True)
class EdgeEndpoints:
    """끝단 서브픽셀 x 좌표(gray_roi 로컬 좌표계). 프레임 좌표 환산은 호출자 몫.

    aivis_types 의 공유 LengthResult 스키마는 오케스트레이터 승인 없이 바꾸지
    않으므로, 측정 근거 시각화(끝단/측정선 오버레이)에 필요한 좌표는 이 vision
    내부 dataclass 로 별도 반환한다(measure_length_ex).
    """

    left_x: float   # 좌측(상승 에지) 끝단 x
    right_x: float  # 우측(하강 에지) 끝단 x


@dataclass(frozen=True)
class LengthSpan:
    """측정 스팬(끝단 2점 + 세로 범위) — **원본 프레임 좌표계**. 오버레이 렌더용.

    length_roi offset 을 반영해 프레임 좌표로 환산한 값이라 결과 오버레이
    (render_overlay/render_batch_overlay)가 그대로 그린다. left_x/right_x 가
    None 이면 끝단 미검출(그리지 않음).
    """

    left_x: Optional[float]
    right_x: Optional[float]
    y_top: int
    y_bottom: int

    @property
    def valid(self) -> bool:
        return self.left_x is not None and self.right_x is not None


def _parabolic_subpixel(values: np.ndarray, idx: int) -> float:
    """idx 주변 3점 포물선 정점으로 서브픽셀 위치 보정.

    values: 1D 배열, idx: 정수 극값 위치. 반환: 서브픽셀 위치(float).
    경계면 보정 없이 idx 반환.
    """
    if idx <= 0 or idx >= len(values) - 1:
        return float(idx)
    y0, y1, y2 = (
        float(values[idx - 1]),
        float(values[idx]),
        float(values[idx + 1]),
    )
    denom = y0 - 2.0 * y1 + y2
    if abs(denom) < 1e-9:
        return float(idx)
    delta = 0.5 * (y0 - y2) / denom
    # 보정량이 비정상적으로 크면(±1 초과) 무시
    if abs(delta) > 1.0:
        return float(idx)
    return float(idx) + delta


def _find_edges(
    profile: np.ndarray, min_contrast: float
) -> Optional[Tuple[float, float]]:
    """수평 프로파일에서 좌(상승)/우(하강) 끝단 서브픽셀 위치를 찾는다."""
    if profile.size < 5:
        return None
    # 1차 차분(그래디언트). 평활화로 노이즈 완화.
    smooth = np.convolve(profile, np.ones(3) / 3.0, mode="same")
    grad = np.gradient(smooth)
    # 대비 부족이면 실패.
    if float(np.max(profile) - np.min(profile)) < min_contrast:
        return None
    left_i = int(np.argmax(grad))    # 가장 강한 상승 에지
    right_i = int(np.argmin(grad))   # 가장 강한 하강 에지
    if right_i <= left_i:
        return None
    # 에지 강도가 충분한지 확인
    if abs(grad[left_i]) < 1e-3 or abs(grad[right_i]) < 1e-3:
        return None
    left = _parabolic_subpixel(np.abs(grad), left_i)
    right = _parabolic_subpixel(np.abs(grad), right_i)
    if right <= left:
        return None
    return left, right


def measure_length_ex(
    gray_roi: np.ndarray,
    item: ItemMaster,
    *,
    min_contrast: float = 20.0,
    mm_per_px_override: Optional[float] = None,
    correct_tilt: bool = True,
    gauge: Optional[FiducialScale] = None,
    roi_x_offset: float = 0.0,
) -> Tuple[LengthResult, Optional[EdgeEndpoints]]:
    """길이 측정 + 끝단 좌표(측정 근거 시각화용) 동시 반환.

    measure_length 의 단일 진실원. 반환하는 EdgeEndpoints 는 gray_roi 로컬 x
    좌표라, 오버레이는 length_roi offset 을 더해 프레임 좌표로 환산한다.
    끝단 검출 실패 시 endpoints=None.

    mm_per_px_override: 이 프레임에서 기준자로 **직접 잰** 스케일. 주면
      item.px_to_mm_scale 대신 쓴다(배율 흔들림 상쇄, §calib/fiducial).
    gauge: 기준자를 **자로 읽기** 위한 FiducialScale. 주면 곱셈 대신
      gauge.span_mm(좌,우) 로 읽는다 — 끝단 옆 마크의 국소 배율이 적용되어
      렌즈 왜곡 잔차·원근이 함께 지워진다(가장 정확한 경로).
      이때 roi_x_offset 으로 ROI 로컬 x 를 **프레임 좌표**로 올려야 한다.
    roi_x_offset: gray_roi 의 좌상단 x (프레임 좌표). gauge 사용 시 필수.
    correct_tilt: 기울기 보정 사용. 끄면 종전(수평거리) 동작 — 회귀 비교용이며
      운영에서 끌 이유는 없다.

    우선순위: gauge > mm_per_px_override > item.px_to_mm_scale.
    """
    t0 = time.perf_counter()
    ref = float(item.ref_length_mm)
    scale = (
        float(mm_per_px_override)
        if mm_per_px_override is not None and mm_per_px_override > 0
        else float(item.px_to_mm_scale)
    )

    edges = None
    if gray_roi is not None and gray_roi.ndim == 2 and gray_roi.shape[1] >= 5:
        # 세로 평균으로 1D 수평 프로파일 → 끝단의 수직 에지를 강조.
        profile = gray_roi.astype(np.float32).mean(axis=0)
        edges = _find_edges(profile, min_contrast)

    if edges is None:
        elapsed = int(round((time.perf_counter() - t0) * 1000))
        # 끝단 검출 실패 → 오류 알림 대상(M3 DoD), NG.
        return (
            LengthResult(
                ref_length_mm=ref,
                meas_length_mm=None,
                deviation_mm=None,
                length_verdict=Verdict.NG,
                edge_detected=False,
                proc_time_ms=elapsed,
            ),
            None,
        )

    left, right = edges
    pixel_distance = right - left
    fit: Optional[EdgeLineFit] = None
    if correct_tilt:
        candidate = fit_edge_lines(gray_roi, left, right)
        # 게이트를 통과한 적합만 측정을 덮어쓴다. 약한 적합이 이기면 보정이
        # 아니라 새 오차가 된다(행 4개짜리 기울기는 노이즈다).
        if candidate is not None and candidate.reject_reason(gray_roi.shape[0]) is None:
            fit = candidate
            # 기울어진 두 절단면 사이의 수직거리가 참 길이다.
            pixel_distance = fit.perpendicular_px
            # 적합이 준 중앙 x 가 세로평균 추정보다 정밀하다 — 오버레이도 맞춘다.
            left, right = fit.left.intercept, fit.right.intercept
    if gauge is not None:
        # 자 읽기. 기울기 보정은 수직거리를 주므로, 수평으로 읽은 값에
        # cos 를 다시 곱해 일관되게 맞춘다.
        span = gauge.span_mm(roi_x_offset + left, roi_x_offset + right)
        if fit is not None:
            span *= pixel_distance / (right - left) if right > left else 1.0
        meas_mm = round(span, 3)
    else:
        meas_mm = round(pixel_distance * scale, 3)
    deviation = round(meas_mm - ref, 3)
    ok = (-float(item.tol_minus_mm)) <= deviation <= float(item.tol_plus_mm)
    verdict = Verdict.OK if ok else Verdict.NG

    elapsed = int(round((time.perf_counter() - t0) * 1000))
    return (
        LengthResult(
            ref_length_mm=ref,
            meas_length_mm=meas_mm,
            deviation_mm=deviation,
            length_verdict=verdict,
            edge_detected=True,
            proc_time_ms=elapsed,
        ),
        EdgeEndpoints(left_x=float(left), right_x=float(right)),
    )


def measure_length(
    gray_roi: np.ndarray,
    item: ItemMaster,
    *,
    min_contrast: float = 20.0,
    mm_per_px_override: Optional[float] = None,
) -> LengthResult:
    """길이 측정. gray_roi 는 length_roi 로 크롭한 그레이(전처리 보정본 권장).

    item.px_to_mm_scale, ref_length_mm, tol_plus_mm, tol_minus_mm 사용
    (임계/보정계수 하드코딩 금지 — 모두 ItemMaster 에서 읽는다). 끝단 좌표까지
    필요하면 measure_length_ex 를 쓴다(이 함수는 그 얇은 래퍼).
    """
    result, _endpoints = measure_length_ex(
        gray_roi, item,
        min_contrast=min_contrast,
        mm_per_px_override=mm_per_px_override,
    )
    return result
