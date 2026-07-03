"""표면 결함 고전 CV 폴백 (M4, §6.3).

각 항목 점수는 0~1 신뢰도. 모두 결정적(무작위성 없음).
점수는 마스크(파이프 전경) 내부에서만 계산해 배경 영향을 배제한다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np
from aivis_types import DefectCode, ItemMaster, SurfaceResult, Verdict


@dataclass
class ScratchLocation:
    """스크래치 위치(표면 ROI 좌표계 bbox + 길이 px)."""

    x0: int
    y0: int
    x1: int
    y1: int
    length_px: float


@dataclass
class SurfaceScores:
    """3종 점수 + 스크래치 위치(시각화/저장용)."""

    oil_score: float
    discolor_score: float
    scratch_score: float
    scratch_locations: List[ScratchLocation] = field(default_factory=list)


def _clip01(v: float) -> float:
    return float(max(0.0, min(1.0, v)))


def _foreground_mask(
    region_bgr: np.ndarray, mask: Optional[np.ndarray]
) -> np.ndarray:
    """표면 영역 내 전경 마스크(bool). mask 미제공 시 전체 True."""
    h, w = region_bgr.shape[:2]
    if mask is None:
        return np.ones((h, w), dtype=bool)
    m = mask
    if m.shape[:2] != (h, w):
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
    return m > 0


def score_oil(region_bgr: np.ndarray, fg: np.ndarray) -> float:
    """유분기: 반사 하이라이트 포화 패치 비율.

    유분/오염은 균일 확산광에서 국소 반사 포화(매우 밝은 얼룩)로 나타난다.
    전경 내 밝기 상위 포화 픽셀의 면적 비율을 점수화.
    """
    gray = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY)
    fg_vals = gray[fg]
    if fg_vals.size == 0:
        return 0.0
    # 포화에 가까운(>=245) 픽셀 비율 + 국소 표준편차(얼룩) 가중.
    sat_ratio = float(np.mean(fg_vals >= 245))
    # 작은 밝은 블롭(얼룩) 강조: 톱햇.
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
    blob_ratio = float(np.mean((tophat[fg] >= 40)))
    score = sat_ratio * 6.0 + blob_ratio * 3.0
    return _clip01(score)


def score_discolor(region_bgr: np.ndarray, fg: np.ndarray) -> float:
    """변색: LAB 색공간에서 중앙값 대비 색상 이탈 영역 비율.

    은→금→주황→갈색 변색은 a*(적-녹), b*(황-청) 축에서 벗어난다.
    전경 a/b 중앙값 기준 거리가 큰 픽셀의 면적 비율을 점수화.
    """
    lab = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    a = lab[:, :, 1][fg]
    b = lab[:, :, 2][fg]
    if a.size == 0:
        return 0.0
    a_med = float(np.median(a))
    b_med = float(np.median(b))
    dist = np.sqrt((a - a_med) ** 2 + (b - b_med) ** 2)
    # 정상 알루미늄(무채색)은 a,b≈128 근처로 분포가 좁다. 이탈 비율.
    anomaly_ratio = float(np.mean(dist >= 18.0))
    score = anomaly_ratio * 3.5
    return _clip01(score)


def score_scratch(
    region_bgr: np.ndarray, fg: np.ndarray
) -> Tuple[float, List[ScratchLocation]]:
    """스크래치: 사광 가정 선형 에지(긴 가늘고 곧은 결함) 탐지.

    Canny + 모폴로지로 선형 구조를 강조하고, 길게 이어진 컴포넌트를
    스크래치 후보로 본다. 점수 = 후보 총 길이 / 표면 폭 정규화.
    """
    gray = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    # 전경 외부는 무시.
    g = gray.copy()
    g[~fg] = 0
    edges = cv2.Canny(g, 60, 160)
    # 가로 선형 구조 강조(사광 스크래치는 보통 한 방향). 가로/세로 모두 시도.
    locs: List[ScratchLocation] = []
    total_len = 0.0
    for ksize in ((25, 1), (1, 25)):
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, ksize)
        lined = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
        lined = cv2.morphologyEx(lined, cv2.MORPH_OPEN, kernel)
        cnts, _ = cv2.findContours(
            lined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        for c in cnts:
            x, y, cw, ch = cv2.boundingRect(c)
            length = float(max(cw, ch))
            aspect = length / max(1.0, float(min(cw, ch)))
            # 길고 가는 것만 스크래치로 인정.
            if length >= 0.18 * max(w, h) and aspect >= 4.0:
                total_len += length
                locs.append(ScratchLocation(x, y, x + cw, y + ch, length))
    norm = total_len / float(max(w, h))
    score = _clip01(norm * 0.9)
    return score, locs


def analyze_surface(
    surface_region_bgr: np.ndarray,
    item: ItemMaster,
    *,
    mask: Optional[np.ndarray] = None,
) -> SurfaceResult:
    """표면 3종 분석 → SurfaceResult.

    임계값은 ItemMaster 에서 읽는다(None 이면 보수적 기본 0.5 적용하되,
    이는 폴백 안전장치이며 운영은 기준정보로 관리).
    """
    t0 = time.perf_counter()
    fg = _foreground_mask(surface_region_bgr, mask)

    oil = score_oil(surface_region_bgr, fg)
    dis = score_discolor(surface_region_bgr, fg)
    scr, _locs = score_scratch(surface_region_bgr, fg)

    oil_th = item.oil_threshold if item.oil_threshold is not None else 0.5
    dis_th = (
        item.discolor_threshold if item.discolor_threshold is not None else 0.5
    )
    scr_th = (
        item.scratch_threshold if item.scratch_threshold is not None else 0.5
    )

    codes: List[DefectCode] = []
    if oil >= float(oil_th):
        codes.append(DefectCode.OIL)
    if dis >= float(dis_th):
        codes.append(DefectCode.DIS)
    if scr >= float(scr_th):
        codes.append(DefectCode.SCR)

    verdict = Verdict.NG if codes else Verdict.OK
    elapsed = int(round((time.perf_counter() - t0) * 1000))
    return SurfaceResult(
        oil_score=round(oil, 4),
        discolor_score=round(dis, 4),
        scratch_score=round(scr, 4),
        surface_verdict=verdict,
        defect_codes=codes,
        proc_time_ms=elapsed,
    )
