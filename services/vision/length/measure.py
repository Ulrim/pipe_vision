"""서브픽셀 길이 측정 (M3, §6.2).

알고리즘(결정적):
1) 전처리 ROI(length_roi) 내 그레이를 세로방향으로 평균 → 1D 수평 프로파일.
   파이프는 밝고 배경은 어두우므로 좌/우 끝단에서 급격한 그래디언트가 생긴다.
2) 프로파일의 그래디언트(소벨/차분)에서 좌측 최대(상승 에지), 우측 최소(하강 에지)
   위치를 픽셀 단위로 찾는다.
3) 그래디언트 극값 주변 3점 포물선 피팅으로 서브픽셀 끝단 좌표를 구한다.
4) pixel_distance = right_edge - left_edge.
5) length_mm = pixel_distance × px_to_mm_scale(ItemMaster).
6) deviation = meas - ref; OK = -tol_minus <= deviation <= +tol_plus.

끝단 검출 실패(프로파일 평탄/대비 부족) → edge_detected=False, NG.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

import numpy as np
from aivis_types import ItemMaster, LengthResult, Verdict


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


def measure_length(
    gray_roi: np.ndarray,
    item: ItemMaster,
    *,
    min_contrast: float = 20.0,
) -> LengthResult:
    """길이 측정. gray_roi 는 length_roi 로 크롭한 그레이(전처리 보정본 권장).

    item.px_to_mm_scale, ref_length_mm, tol_plus_mm, tol_minus_mm 사용
    (임계/보정계수 하드코딩 금지 — 모두 ItemMaster 에서 읽는다).
    """
    t0 = time.perf_counter()
    ref = float(item.ref_length_mm)
    scale = float(item.px_to_mm_scale)

    edges = None
    if gray_roi is not None and gray_roi.ndim == 2 and gray_roi.shape[1] >= 5:
        # 세로 평균으로 1D 수평 프로파일 → 끝단의 수직 에지를 강조.
        profile = gray_roi.astype(np.float32).mean(axis=0)
        edges = _find_edges(profile, min_contrast)

    if edges is None:
        elapsed = int(round((time.perf_counter() - t0) * 1000))
        # 끝단 검출 실패 → 오류 알림 대상(M3 DoD), NG.
        return LengthResult(
            ref_length_mm=ref,
            meas_length_mm=None,
            deviation_mm=None,
            length_verdict=Verdict.NG,
            edge_detected=False,
            proc_time_ms=elapsed,
        )

    left, right = edges
    pixel_distance = right - left
    meas_mm = round(pixel_distance * scale, 3)
    deviation = round(meas_mm - ref, 3)
    ok = (-float(item.tol_minus_mm)) <= deviation <= float(item.tol_plus_mm)
    verdict = Verdict.OK if ok else Verdict.NG

    elapsed = int(round((time.perf_counter() - t0) * 1000))
    return LengthResult(
        ref_length_mm=ref,
        meas_length_mm=meas_mm,
        deviation_mm=deviation,
        length_verdict=verdict,
        edge_detected=True,
        proc_time_ms=elapsed,
    )
