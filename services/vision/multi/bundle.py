"""단면 번들(端面 束) 개수 검출 — 크레이트 적재 1차 스크리닝 (CLAUDE.md §A.1).

**왜 필요한가.** 부록 A.0 에서 1차 촬영분을 평가하며 "단면을 위에서 본 다발
사진" 은 *다객체·겹침이라 개별 판정·길이 측정이 불가* 하다고 적었다. 그건
맞다. 하지만 §A.1 은 같은 구도를 버리지 않고 **크레이트 단면 면스캔을
변색·유분기 1차 스크리닝 옵션으로 검토** 한다고 남겨두었다. 그 구도에서
가장 먼저 필요한 것이 *프레임 안에 파이프 단면이 몇 개 있고 어디 있는가* 다.
개수를 세야 적재 수량을 검수할 수 있고, 위치를 알아야 단면별로 색·유분기를
볼 수 있다.

`multi/segment.py` 의 `segment_tubes()` 로는 안 된다. 그쪽은 **옆으로 누운**
튜브가 나란히 쌓인 장면을 1차원 밝기 프로파일의 골로 가르는 알고리즘이고
상한도 20개다. 단면 번들은 2차원으로 조밀 충전된 원이 수백 개라 가정이
전혀 다르다.

알고리즘(결정적, numpy/cv2 만 — 라즈베리파이 CPU):
1) 그레이 + CLAHE. 창고 조명은 한쪽만 밝아 전역 임계가 듣지 않는다.
2) 적응형 임계로 **보어(관 안쪽, 주변보다 어두움)** 마스크를 만든다.
3) 열림 연산으로 잡티 제거.
4) 거리변환 → 국소 최대(팽창 비교)를 보어 1개당 씨앗 1개로 잡는다.
   조밀 충전이라 보어끼리 붙는데, 거리변환 봉우리는 보어마다 하나씩 선다.
5) 봉우리 거리값의 중앙값으로 반지름을 추정하고, 그보다 가까운 씨앗은
   하나로 합친다(비최대 억제).

반환은 중심 좌표와 반지름이라, 상위에서 단면별 ROI 를 잘라 색/유분기
기술자를 돌릴 수 있다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np


@dataclass(frozen=True)
class BundleDetection:
    """단면 1개. (cx, cy) 중심, r 반지름(px), score 거리변환 봉우리 값."""

    cx: float
    cy: float
    r: float
    score: float


@dataclass(frozen=True)
class BundleResult:
    count: int
    detections: List[BundleDetection]
    median_radius: float
    proc_time_ms: int


def _clahe(gray: np.ndarray) -> np.ndarray:
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)


def _bore_mask(gray: np.ndarray, block: int, c: int) -> np.ndarray:
    """보어(어두운 안쪽) = 국소 평균보다 어두운 화소."""
    if block % 2 == 0:
        block += 1
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, c
    )


def _peaks(dist: np.ndarray, min_dist: int, min_val: float) -> np.ndarray:
    """거리변환의 국소 최대 좌표 (N,2) [y,x]. 팽창 비교 — scipy 불필요."""
    k = max(3, int(min_dist) | 1)
    dil = cv2.dilate(dist, np.ones((k, k), np.uint8))
    peak = (dist >= dil) & (dist >= min_val)
    ys, xs = np.nonzero(peak)
    return np.stack([ys, xs], axis=1) if len(ys) else np.empty((0, 2), np.int64)


def _nms(pts: np.ndarray, vals: np.ndarray, min_dist: float) -> np.ndarray:
    """가까운 씨앗 병합. 봉우리 값이 큰 것부터 남긴다(결정적)."""
    if len(pts) == 0:
        return pts
    order = np.lexsort((pts[:, 1], pts[:, 0], -vals))  # 값 내림차순, 동률은 좌표순
    keep: List[int] = []
    taken = np.zeros((0, 2), np.float64)
    d2 = float(min_dist) ** 2
    for i in order:
        p = pts[i].astype(np.float64)
        if len(taken) and np.min(((taken - p) ** 2).sum(axis=1)) < d2:
            continue
        keep.append(int(i))
        taken = np.vstack([taken, p])
    return pts[np.array(sorted(keep), dtype=np.int64)]


def count_bundle(
    bgr: np.ndarray,
    *,
    block: int = 41,
    c: int = 5,
    open_ksize: int = 3,
    min_radius_px: float = 4.0,
    nms_scale: float = 2.4,
) -> BundleResult:
    """단면 번들 프레임 → 개별 단면 검출.

    `nms_scale` 은 추정 반지름 대비 억제 반경 배율이다. 조밀 충전에서는
    중심 간 거리가 2r 에 가깝지만, 겹쳐 보이는 뒤쪽 열 때문에 그대로 쓰면
    과소검출된다. 기본값은 공개 데이터셋 전수 격자탐색에서 F1 이 가장 높았던
    (block=41, c=5, nms_scale=2.4) 조합이다.
    """
    t0 = time.perf_counter()
    if bgr is None or bgr.size == 0:
        return BundleResult(0, [], 0.0, 0)
    gray = bgr if bgr.ndim == 2 else cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = _clahe(gray)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    mask = _bore_mask(gray, block, c)
    if open_ksize >= 3:
        k = np.ones((open_ksize, open_ksize), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)

    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 3)
    if float(dist.max()) < min_radius_px:
        return BundleResult(0, [], 0.0, int((time.perf_counter() - t0) * 1000))

    # 1차 봉우리로 반지름을 추정한 뒤, 그 반지름으로 억제 반경을 다시 잡는다.
    rough = _peaks(dist, min_dist=5, min_val=min_radius_px)
    if len(rough) == 0:
        return BundleResult(0, [], 0.0, int((time.perf_counter() - t0) * 1000))
    r_est = float(np.median(dist[rough[:, 0], rough[:, 1]]))
    r_est = max(r_est, min_radius_px)

    pts = _peaks(dist, min_dist=max(3, int(r_est)), min_val=min_radius_px)
    vals = dist[pts[:, 0], pts[:, 1]]
    pts = _nms(pts, vals, min_dist=r_est * nms_scale)

    dets = [
        BundleDetection(
            cx=float(x), cy=float(y), r=float(dist[y, x]), score=float(dist[y, x])
        )
        for y, x in pts
    ]
    med_r = float(np.median([d.r for d in dets])) if dets else 0.0
    return BundleResult(
        count=len(dets),
        detections=dets,
        median_radius=med_r,
        proc_time_ms=int((time.perf_counter() - t0) * 1000),
    )
