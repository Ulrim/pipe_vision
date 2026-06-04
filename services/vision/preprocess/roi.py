"""ROI 분리 + 반사 보정 + 노이즈 제거 (M2).

흐름(결정적):
1) BGR → GRAY.
2) Otsu 이진화로 파이프(밝은 전경) 분리. 모폴로지 열림/닫힘으로 정리.
3) 최대 연결요소의 바운딩박스 = 제품 ROI.
4) 길이영역(파이프 전체 ROI)과 표면영역(끝단 인셋한 안쪽)을 구분.
5) 반사 보정: 큰 가우시안으로 추정한 조명 성분 나눗셈(평탄화) + CLAHE.
6) 노이즈 제거: 약한 미디언/가우시안.

ROI 결정성: 동일 입력 → 동일 출력(threshold/contour 모두 결정적).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np


@dataclass(frozen=True)
class Roi:
    """직사각형 ROI (이미지 좌표). x0<=x1, y0<=y1."""

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    def crop(self, img: np.ndarray) -> np.ndarray:
        return img[self.y0 : self.y1, self.x0 : self.x1]

    def as_tuple(self) -> Tuple[int, int, int, int]:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass
class PreprocessResult:
    """전처리 산출물.

    - gray_corrected: 반사 보정+정규화된 그레이(전체 프레임 크기).
    - mask: 파이프 전경 이진 마스크(uint8 0/255).
    - length_roi: 길이 측정 영역(파이프 전체 바운딩박스).
    - surface_roi: 표면 판정 영역(끝단을 인셋한 안쪽).
    - found: 파이프 영역 검출 성공 여부.
    """

    gray_corrected: np.ndarray
    mask: np.ndarray
    length_roi: Optional[Roi]
    surface_roi: Optional[Roi]
    found: bool
    proc_time_ms: int = 0


def _correct_reflection(gray: np.ndarray) -> np.ndarray:
    """금속 반사/불균일 조명 보정 (결정적).

    큰 커널 가우시안으로 저주파 조명 성분을 추정해 나눗셈으로 평탄화하고,
    CLAHE 로 국소 대비를 표준화한다.
    """
    g = gray.astype(np.float32) + 1.0
    # 커널은 이미지 크기에 비례(홀수 보장). 저주파 조명 추정.
    k = max(31, (min(gray.shape[:2]) // 8) | 1)
    illum = cv2.GaussianBlur(g, (k, k), 0)
    flat = (g / (illum + 1e-6)) * 128.0
    flat = np.clip(flat, 0, 255).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(flat)


def _denoise(gray: np.ndarray) -> np.ndarray:
    """약한 노이즈 제거(결정적). 끝단 에지를 죽이지 않도록 보수적."""
    return cv2.medianBlur(gray, 3)


def segment_pipe_roi(
    img_bgr: np.ndarray, surface_inset_ratio: float = 0.06
) -> Tuple[np.ndarray, Optional[Roi], Optional[Roi]]:
    """파이프 ROI 분리. (mask, length_roi, surface_roi) 반환.

    surface_inset_ratio: 표면 ROI 를 길이 ROI 양 끝에서 안쪽으로 줄이는 비율
    (끝단 에지/그림자가 표면 판정 오탐을 일으키지 않도록).
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    # Otsu: 밝은 파이프 vs 어두운 배경. 결정적.
    _t, binary = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    # 모폴로지 정리(작은 점/구멍 제거).
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)

    cnts, _ = cv2.findContours(
        binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not cnts:
        return binary, None, None
    # 최대 면적 컨투어 = 파이프.
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < 50:  # 잡음만 있는 경우
        return binary, None, None
    x, y, w, h = cv2.boundingRect(c)

    # 전경만 남긴 깨끗한 마스크(최대 컨투어 채움).
    mask = np.zeros_like(binary)
    cv2.drawContours(mask, [c], -1, 255, thickness=cv2.FILLED)

    length_roi = Roi(x, y, x + w, y + h)
    inset = int(round(w * surface_inset_ratio))
    sx0 = min(x + inset, x + w - 1)
    sx1 = max(x + w - inset, sx0 + 1)
    surface_roi = Roi(sx0, y, sx1, y + h)
    return mask, length_roi, surface_roi


def preprocess(
    img_bgr: np.ndarray, surface_inset_ratio: float = 0.06
) -> PreprocessResult:
    """전체 전처리 파이프라인. 결정적이며 proc_time_ms 를 계측한다."""
    t0 = time.perf_counter()
    if img_bgr is None or img_bgr.ndim != 3:
        raise ValueError("preprocess: BGR 3채널 이미지가 필요하다")

    mask, length_roi, surface_roi = segment_pipe_roi(img_bgr, surface_inset_ratio)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    corrected = _correct_reflection(gray)
    corrected = _denoise(corrected)

    elapsed = int(round((time.perf_counter() - t0) * 1000))
    return PreprocessResult(
        gray_corrected=corrected,
        mask=mask,
        length_roi=length_roi,
        surface_roi=surface_roi,
        found=length_roi is not None,
        proc_time_ms=elapsed,
    )
