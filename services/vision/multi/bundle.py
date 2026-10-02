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

**측정 결과.** 피사체에 따라 성능이 갈린다.

    에이엠피 크레이트(중공 튜브, 근접·고대비) : 번들 내부 사실상 1:1 검출
    Steel Pipe(중공 강관, 창고 원거리·가림)   : F1 0.359 (정답 22,187)
    Steel-bar(속이 찬 봉강)                   : F1 0.063 (정답 14,814)

이 알고리즘은 **어두운 보어**를 찾는다. 봉강은 속이 차서 보어가 없으니
F1 0.06 은 방법의 한계가 아니라 **데이터셋이 제품과 다르다는 증거**다.
창고 영상은 중공이지만 멀고 가려서 중간 점수가 나온다. 제품 성능을 공개
데이터셋 점수로 예측하면 안 된다. 자세한 내용은
docs/PUBLIC_DATASET_REVIEW.md §11~14.

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
from typing import List, Optional

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


def _scale_hint(gray: np.ndarray) -> float:
    """Otsu 전역 임계로 보어 반지름을 대략 잡는다(적응형 블록 크기 결정용).

    적응형 임계의 블록이 보어보다 작으면 **보어 안쪽이 국소 평균과 비슷해져
    어둡다고 판정되지 않는다.** 그러면 보어가 가장자리 조각만 남아 반지름이
    과소추정되고, 억제 반경도 같이 작아져 한 보어를 여러 번 센다. 에이엠피
    크레이트 영상(보어 지름 약 44px)에서 block=41 로 20배 과검출이 났다.
    그래서 블록을 영상에서 추정한 크기에 맞춘다.
    """
    _, m = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    d = cv2.distanceTransform(m, cv2.DIST_L2, 3)
    if float(d.max()) < 2.0:
        return 0.0
    pk = _peaks(d, min_dist=5, min_val=2.0)
    if len(pk) == 0:
        return 0.0
    return float(np.percentile(d[pk[:, 0], pk[:, 1]], 90))


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
    block: Optional[int] = None,
    c: int = 5,
    open_ksize: int = 3,
    close_ratio: float = 0.30,
    radius_percentile: float = 90.0,
    min_radius_px: float = 4.0,
    min_radius_ratio: float = 0.55,
    nms_scale: float = 1.8,
) -> BundleResult:
    """단면 번들 프레임 → 개별 단면 검출.

    **2단계로 돈다.** 1단계에서 보어 반지름을 대략 잡고, 그 크기에 비례한
    커널로 닫힘 연산을 한 뒤 2단계에서 다시 센다. 고정 커널을 쓰면 안 된다 —
    보어가 작은 영상에서는 이웃 보어끼리 붙어버려 번들 전체가 한 덩어리가
    된다(합성 테스트에서 실제로 1개로 셌다).

    - `close_ratio`: 닫힘 커널 = 반지름 × 이 값. 보어 사이 벽 두께보다
      작아야 이웃이 안 붙고, 보어 안 반사 얼룩보다는 커야 조각이 메워진다.
      에이엠피 크레이트 영상 기준 벽 간격은 반지름의 약 0.5배라 0.3 으로 둔다.
    - `radius_percentile`: 억제 반경의 기준. 중앙값을 쓰면 남은 가짜 봉우리가
      값을 끌어내려 억제 반경이 실제 피치보다 작아지고 중복이 살아남는다.
    """
    t0 = time.perf_counter()
    if bgr is None or bgr.size == 0:
        return BundleResult(0, [], 0.0, 0)
    gray = bgr if bgr.ndim == 2 else cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = _clahe(gray)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    if block is None:
        # 추정 반지름의 약 4배를 블록으로 — 보어보다 충분히 커야 한다.
        r_hint = _scale_hint(gray)
        block = 41 if r_hint <= 0 else int(max(31, min(301, round(r_hint * 4))))
    base = _bore_mask(gray, block, c)
    if open_ksize >= 3:
        base = cv2.morphologyEx(
            base, cv2.MORPH_OPEN, np.ones((open_ksize, open_ksize), np.uint8)
        )

    def _radius(mask: np.ndarray) -> tuple:
        d = cv2.distanceTransform(mask, cv2.DIST_L2, 3)
        if float(d.max()) < min_radius_px:
            return d, None
        pk = _peaks(d, min_dist=5, min_val=min_radius_px)
        if len(pk) == 0:
            return d, None
        r = float(np.percentile(d[pk[:, 0], pk[:, 1]], radius_percentile))
        return d, max(r, min_radius_px)

    dist, r0 = _radius(base)
    if r0 is None:
        return BundleResult(0, [], 0.0, int((time.perf_counter() - t0) * 1000))

    # 2단계: 반지름에 비례한 커널로 보어 내부 반사 조각을 메우고 다시 잰다.
    k = int(round(r0 * close_ratio))
    if k >= 2:
        kc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k | 1, k | 1))
        closed = cv2.morphologyEx(base, cv2.MORPH_CLOSE, kc)
        d2, r2 = _radius(closed)
        if r2 is not None:
            dist, r0 = d2, r2

    pts = _peaks(dist, min_dist=max(3, int(r0)), min_val=min_radius_px)
    if len(pts) == 0:
        return BundleResult(0, [], 0.0, int((time.perf_counter() - t0) * 1000))
    vals = dist[pts[:, 0], pts[:, 1]]
    pts = _nms(pts, vals, min_dist=r0 * nms_scale)

    # 튜브 셋이 만나는 삼각형 틈도 어두워서 보어로 잡힌다. 그 틈의 내접원은
    # 보어보다 뚜렷하게 작으므로 반지름 비율로 걸러낸다.
    floor_r = max(min_radius_px, r0 * min_radius_ratio)
    dets = [
        BundleDetection(
            cx=float(x), cy=float(y), r=float(dist[y, x]), score=float(dist[y, x])
        )
        for y, x in pts
        if float(dist[y, x]) >= floor_r
    ]
    med_r = float(np.median([d.r for d in dets])) if dets else 0.0
    return BundleResult(
        count=len(dets),
        detections=dets,
        median_radius=med_r,
        proc_time_ms=int((time.perf_counter() - t0) * 1000),
    )
