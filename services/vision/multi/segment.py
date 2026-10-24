"""다객체(다중 튜브) 분할 (M2 확장, §6.2 고전 CV).

한 프레임에 축이 나란히(서로 붙어) 눕힌 알루미늄 튜브 다수를 개별 스트립으로
분할한다. 원통 튜브는 crown(밝은 마루)과 인접 튜브 사이 seam(어두운 골)이
축에 수직 방향으로 주기적으로 나타난다. 이 밝기 프로파일의 골(valley)을
경계로 삼아 N개 스트립(튜브별 bbox)으로 나눈다.

알고리즘(결정적, scipy 없이 numpy/cv2 만):
1) axis 로 스캔 방향 결정. axis="horizontal"(튜브가 가로로 누움)이면 튜브는
   세로로 쌓이므로 행(row) 방향 프로파일을 본다. "vertical"은 전치해 동일 처리.
2) Otsu 이진화 → 전경(튜브 밴드) 커버리지로 튜브가 존재하는 밴드[lo,hi] 검출.
3) 밴드 내부에서 튜브 길이축 중앙부만 평균해 수직(perpendicular) 밝기 프로파일 계산.
   짧은 튜브(LEN 결함)의 배경 혼입을 줄이기 위해 길이축 중앙 60% 만 사용.
4) 프로파일 스무딩 후:
   - expected_count 주어지면: 밴드를 N등분한 기대 위치 근방에서 국소 최소를
     스냅 → N-1 개 seam.
   - 없으면: 국소 최소의 prominence(지형학적 돌출도)로 seam 후보를 고르고,
     자기상관으로 추정한 피치로 최소 간격을 강제해 자동으로 N 추정.
5) 경계[band_lo, seam_1..seam_{N-1}, band_hi] → 각 스트립의 실제 길이축 범위를
   전경에서 재추정해 튜브별 bbox 를 만든다.

결정적: 모든 연산이 결정적(Otsu/평균/정렬). 동일 입력 → 동일 출력.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

from ..preprocess.area import InspectArea, area_rect
from ..preprocess.foreground import (
    contrast_mask,
    dominant_angle,
    label_blobs,
    light_background,
    select_tubes,
)

# 한 프레임에서 분할할 수 있는 튜브 수의 안전 상한.
#
# 처음에 20 으로 잡았는데 **현장 사진을 보고 틀렸음을 확인했다.** 에이엠피는
# 다발 동시 절단이 상시 공정이라(도입기업 확인) 절단기 클램프에만 11~12단이
# 적층되고, 출력 랙에는 수십 개가 나란히 눕는다. 20 은 현실보다 작다.
#
# 더 나쁜 것은 **조용히 잘렸다는 점**이다. 기준정보에 expected_count=40 을
# 넣어도 분할기가 말없이 20 으로 깎아 20개만 돌려줬다. 설정이 무시된 채
# 검사 결과가 나오면 수량이 틀린 것을 아무도 모른다. 그래서 상한을 올리고,
# 넘는 설정은 API 스키마에서 **거부**되게 했다(aivis_types).
#
# 설비가 더 큰 다발을 쓰면 AIVIS_MAX_TUBES 로 올린다.
MAX_TUBES_HARD = int(os.getenv("AIVIS_MAX_TUBES", "64") or 64)


@dataclass(frozen=True)
class TubeROI:
    """단일 튜브 스트립 ROI.

    - index: 축 순서상 위치(1..N).
    - (x0,y0,x1,y1): 이미지 좌표 bbox (x0<=x1, y0<=y1).
    - confidence: seam 대비 crown 돌출도로 산출한 분할 신뢰도(0~1).
    """

    index: int
    x0: int
    y0: int
    x1: int
    y1: int
    confidence: float

    @property
    def bbox(self) -> Tuple[int, int, int, int]:
        return (self.x0, self.y0, self.x1, self.y1)

    def crop(self, img: np.ndarray) -> np.ndarray:
        return img[self.y0 : self.y1, self.x0 : self.x1]


def _clip01(v: float) -> float:
    return float(max(0.0, min(1.0, v)))


def _smooth(p: np.ndarray, win: int) -> np.ndarray:
    if win < 3:
        return p.astype(np.float32)
    win = win | 1  # 홀수 보장
    k = np.ones(win, dtype=np.float32) / float(win)
    return np.convolve(p.astype(np.float32), k, mode="same")


def _foreground_band(
    binm: np.ndarray, cov_thresh: float = 0.3
) -> Optional[Tuple[int, int]]:
    """행 커버리지로 튜브 밴드[lo,hi) 를 찾는다(단일 층 가정).

    **틈을 메운 뒤 최장 연속 구간**을 쓴다. 두 가지를 동시에 피해야 한다.

    - 예전처럼 임계를 넘는 행의 min~max 를 쓰면, 밴드와 멀리 떨어진 밝은 배경
      한 줄만 걸려도 밴드가 화면 전체로 늘어난다. 현장 사진(흰 양동이·창문이
      금속만큼 밝다)에서 실제로 배경까지 튜브 띠로 잘렸다.
    - 그렇다고 그냥 최장 구간을 쓰면, 튜브 사이 seam(어두운 골)에서 커버리지가
      임계 아래로 떨어져 밴드가 튜브마다 조각나고 결국 1개만 남는다.

    그래서 seam 폭 정도의 틈은 메우고(gap_tol), 그 뒤 가장 긴 덩어리를 고른다.
    배경은 밴드에서 충분히 떨어져 있어 메워지지 않는다.
    """
    cov = (binm > 0).mean(axis=1)  # 행별 전경 비율
    hot = cov >= cov_thresh
    if not hot.any():
        return None

    # seam 은 좁고 배경은 멀다 — 그 사이를 가르는 틈 허용치.
    gap_tol = max(3, int(round(len(hot) * 0.05)))
    filled = hot.copy()
    idx = np.flatnonzero(hot)
    for a, b in zip(idx[:-1], idx[1:]):
        if 1 < b - a <= gap_tol + 1:
            filled[a + 1 : b] = True

    best = (0, 0, 0)  # (길이, lo, hi)
    run_lo = None
    for i, v in enumerate(filled):
        if v and run_lo is None:
            run_lo = i
        elif not v and run_lo is not None:
            if i - run_lo > best[0]:
                best = (i - run_lo, run_lo, i)
            run_lo = None
    if run_lo is not None and len(filled) - run_lo > best[0]:
        best = (len(filled) - run_lo, run_lo, len(filled))

    lo, hi = int(best[1]), int(best[2])
    if hi - lo < 3:
        return None
    return lo, hi


def _length_span(
    binm: np.ndarray, band_lo: int, band_hi: int, cov_thresh: float = 0.3
) -> Tuple[int, int]:
    """밴드 내 튜브 길이축(열) 범위를 찾는다."""
    sub = binm[band_lo:band_hi, :]
    col_cov = (sub > 0).mean(axis=0)
    cols = np.where(col_cov >= cov_thresh)[0]
    if cols.size == 0:
        return 0, binm.shape[1]
    return int(cols.min()), int(cols.max()) + 1


def _prominence(profile: np.ndarray, idx: int) -> float:
    """국소 최소 idx 의 지형학적 돌출도(반전 prominence)."""
    v = float(profile[idx])
    left_max = v
    i = idx - 1
    while i >= 0 and profile[i] >= v:
        if profile[i] > left_max:
            left_max = float(profile[i])
        i -= 1
    right_max = v
    j = idx + 1
    n = len(profile)
    while j < n and profile[j] >= v:
        if profile[j] > right_max:
            right_max = float(profile[j])
        j += 1
    return min(left_max, right_max) - v


def _local_minima(profile: np.ndarray, lo: int, hi: int) -> List[int]:
    """(lo,hi) 구간 내부의 국소 최소 인덱스. 플래토는 시작점 1개만."""
    mins: List[int] = []
    for i in range(lo + 1, hi - 1):
        if profile[i] <= profile[i - 1] and profile[i] < profile[i + 1]:
            mins.append(i)
    return mins


def _estimate_pitch(profile: np.ndarray, lo: int, hi: int, max_tubes: int) -> int:
    """자기상관으로 튜브 피치(주기) 추정. 실패 시 밴드/최대개수 기반 하한."""
    band = profile[lo:hi].astype(np.float32)
    band = band - float(band.mean())
    n = band.size
    if n < 6:
        return max(3, (hi - lo))
    min_lag = max(3, (hi - lo) // (max_tubes + 1))
    max_lag = max(min_lag + 1, (hi - lo) // 2)
    best_lag, best = min_lag, -1e30
    for lag in range(min_lag, max_lag + 1):
        c = float(np.dot(band[:-lag], band[lag:]))
        if c > best:
            best = c
            best_lag = lag
    return best_lag


def _auto_seams(
    profile: np.ndarray,
    band_lo: int,
    band_hi: int,
    min_tubes: int,
    max_tubes: int,
    prom_frac: float = 0.10,
) -> List[int]:
    """자동 seam 검출: prominence 필터 + 자기상관 피치로 최소간격 강제."""
    rng = float(profile[band_lo:band_hi].max() - profile[band_lo:band_hi].min())
    if rng <= 1e-6:
        return []
    cands = _local_minima(profile, band_lo, band_hi)
    if not cands:
        return []
    proms = {i: _prominence(profile, i) for i in cands}
    keep = [i for i in cands if proms[i] >= prom_frac * rng]
    if not keep:
        return []
    pitch = _estimate_pitch(profile, band_lo, band_hi, max_tubes)
    min_sep = max(3, int(round(0.5 * pitch)))
    # prominence 큰 순으로 그리디 선택(최소 간격 유지) → 결정적.
    selected: List[int] = []
    for i in sorted(keep, key=lambda k: (-proms[k], k)):
        if all(abs(i - s) >= min_sep for s in selected):
            selected.append(i)
    selected.sort()
    # 최대 개수 초과 시 돌출도 낮은 seam 제거.
    max_seams = max_tubes - 1
    if len(selected) > max_seams:
        selected = sorted(
            sorted(selected, key=lambda k: -proms[k])[:max_seams]
        )
    return selected


def _expected_seams(
    profile: np.ndarray,
    band_lo: int,
    band_hi: int,
    n: int,
) -> List[int]:
    """expected_count=n 에 맞춰 N-1 개 seam 을 기대위치 근방 국소최소로 스냅."""
    if n <= 1:
        return []
    band_h = band_hi - band_lo
    pitch = band_h / float(n)
    win = max(2, int(round(pitch * 0.4)))
    seams: List[int] = []
    prev = band_lo
    for k in range(1, n):
        center = int(round(band_lo + k * pitch))
        lo = max(prev + 1, center - win)
        hi = min(band_hi - 1, center + win)
        if hi <= lo:
            pos = min(max(center, prev + 1), band_hi - 1)
        else:
            seg = profile[lo : hi + 1]
            pos = lo + int(np.argmin(seg))
            if pos <= prev:
                pos = prev + 1
        seams.append(pos)
        prev = pos
    return seams


def _seam_confidence(
    profile: np.ndarray, boundaries: List[int], k: int
) -> float:
    """스트립 k 의 신뢰도: 양 경계 seam 대비 crown 돌출도 정규화."""
    lo, hi = boundaries[k], boundaries[k + 1]
    if hi - lo < 1:
        return 0.0
    seg = profile[lo:hi]
    rng = float(profile.max() - profile.min())
    if rng <= 1e-6:
        return 0.5
    crown = float(seg.max())
    edge = min(float(profile[lo]), float(profile[min(hi, len(profile) - 1)]))
    return _clip01(0.4 + 0.6 * ((crown - edge) / rng))


def _segment_axis(
    gray: np.ndarray,
    expected_count: Optional[int],
    min_tubes: int,
    max_tubes: int,
    valid: Optional[np.ndarray] = None,
) -> List[Tuple[int, int, int, int, float]]:
    """가로로 누운(행 방향으로 쌓인) 튜브 분할. (x0,y0,x1,y1,conf) 리스트."""
    h, w = gray.shape[:2]
    # 전역 대비 부족(빈/평탄 프레임) → 튜브 없음. Otsu 가 평탄면을 전경으로
    # 오인하는 것을 차단(상위에서 count=0 처리, graceful).
    if float(gray.max()) - float(gray.min()) < 20.0:
        return []
    if light_background(gray):
        return _segment_light(gray, expected_count, min_tubes, max_tubes, valid=valid)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _t, binm = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if valid is not None:
        binm[valid == 0] = 0
    return _segment_band(gray, binm, expected_count, min_tubes, max_tubes)


def _segment_light(
    gray: np.ndarray,
    expected_count: Optional[int],
    min_tubes: int,
    max_tubes: int,
    valid: Optional[np.ndarray] = None,
) -> List[Tuple[int, int, int, int, float]]:
    """밝은·무늬 배경(2026-10-21) — 떨어져 놓인 튜브는 덩어리 하나가 튜브 하나, 붙은 다발은 그 덩어리
    안에서 종전처럼 seam(어두운 골)으로 나눈다.

    종전 밴드 방식은 '튜브가 서로 붙은 다발 하나' 를 가정해, 떨어진 튜브 사이의 빈 행에서 밴드가
    끊겨 하나만 남았다. 덩어리별로 보면 떨어져 있든 붙어 있든 같은 코드로 된다.
    덩어리당 튜브 수 = 폭 ÷ 가장 가는 덩어리 폭(반올림). 덩어리가 하나뿐이면 폭 기준이 없어 seam 자동.
    기대 개수가 덩어리 수와 같으면 하나씩, 덩어리가 하나면 그 안을 기대 개수로 나눈다.
    """
    mask = contrast_mask(gray)
    if valid is not None:
        mask[valid == 0] = 0
    lab, blobs = label_blobs(mask)
    tubes = sorted(select_tubes(blobs), key=lambda b: b.cy)
    if not tubes:
        return []
    unit = min(b.width for b in tubes) if len(tubes) > 1 else None
    per: List[Optional[int]] = [max(1, int(round(b.width / unit))) if unit else None for b in tubes]
    if expected_count:
        if len(tubes) == expected_count:
            per = [1] * len(tubes)
        elif len(tubes) == 1:
            per = [int(expected_count)]
        elif None in per or sum(p or 0 for p in per) != expected_count:
            per = [p if p and p > 1 else (1 if unit else None) for p in per]
    boxes: List[Tuple[int, int, int, int, float]] = []
    for b, n in zip(tubes, per):
        if n == 1:
            boxes.append((b.x0, b.y0, b.x1, b.y1, 1.0))
            continue
        sub_g = gray[b.y0:b.y1, b.x0:b.x1]
        sub_m = ((lab[b.y0:b.y1, b.x0:b.x1] == b.label).astype(np.uint8)) * 255
        inner = _merge_slivers(_segment_band(sub_g, sub_m, n, min_tubes, max_tubes))
        if not inner:
            boxes.append((b.x0, b.y0, b.x1, b.y1, 0.5))
            continue
        boxes.extend((x0 + b.x0, y0 + b.y0, x1 + b.x0, y1 + b.y0, c) for (x0, y0, x1, y1, c) in inner)
    return boxes


def _merge_slivers(
    boxes: List[Tuple[int, int, int, int, float]],
) -> List[Tuple[int, int, int, int, float]]:
    """폭이 중앙값의 절반도 안 되는 조각을 이웃 띠에 붙인다(가로 튜브 — 띠는 y 순서).

    밝은 배경에서 붙은 다발의 바깥 튜브는 가장자리 어두운 테두리가 골처럼 보여, 자동 분할이 다발
    양끝에 얇은 조각을 하나씩 더 만들었다(현장 사진: 4개 → 6개). 튜브 지름은 같으므로 조각은 튜브가 아니다.
    """
    out = list(boxes)
    while len(out) > 1:
        hs = [b[3] - b[1] for b in out]
        med = float(np.median(hs))
        i = int(np.argmin(hs))
        if hs[i] >= 0.5 * med:
            break
        j = i + 1 if i == 0 else (i - 1 if i == len(out) - 1 else (i - 1 if hs[i - 1] <= hs[i + 1] else i + 1))
        a, b = out[min(i, j)], out[max(i, j)]
        merged = (min(a[0], b[0]), a[1], max(a[2], b[2]), b[3], min(a[4], b[4]))
        out[min(i, j):max(i, j) + 1] = [merged]
    return out


def _segment_band(
    gray: np.ndarray,
    binm: np.ndarray,
    expected_count: Optional[int],
    min_tubes: int,
    max_tubes: int,
) -> List[Tuple[int, int, int, int, float]]:
    """전경 마스크(binm)의 튜브 밴드 하나를 seam 으로 나눈다(종전 분할 본체)."""
    band = _foreground_band(binm)
    if band is None:
        return []
    band_lo, band_hi = band

    # 길이축 중앙 60% 만 사용해 수직 프로파일 계산(짧은 튜브 배경 혼입 방지).
    x_lo, x_hi = _length_span(binm, band_lo, band_hi)
    span = x_hi - x_lo
    cx0 = x_lo + int(round(span * 0.2))
    cx1 = x_hi - int(round(span * 0.2))
    if cx1 - cx0 < 5:
        cx0, cx1 = x_lo, x_hi
    profile = gray[:, cx0:cx1].astype(np.float32).mean(axis=1)

    band_h = band_hi - band_lo
    # 스무딩 창: 최소 피치의 1/4 수준으로 seam 을 보존.
    smooth_win = max(3, (band_h // (max_tubes * 4)) | 1)
    sp = _smooth(profile, smooth_win)

    if expected_count is not None:
        n = int(max(min_tubes, min(max_tubes, expected_count)))
        seams = _expected_seams(sp, band_lo, band_hi, n)
    else:
        seams = _auto_seams(sp, band_lo, band_hi, min_tubes, max_tubes)

    boundaries = [band_lo] + seams + [band_hi]
    # 스트립 구성: 실제 길이축 범위 재추정.
    boxes: List[Tuple[int, int, int, int, float]] = []
    for k in range(len(boundaries) - 1):
        y0, y1 = boundaries[k], boundaries[k + 1]
        if y1 - y0 < 2:
            continue
        sub = binm[y0:y1, :]
        col_cov = (sub > 0).mean(axis=0)
        cols = np.where(col_cov >= 0.3)[0]
        if cols.size == 0:
            tx0, tx1 = x_lo, x_hi
        else:
            tx0, tx1 = int(cols.min()), int(cols.max()) + 1
        conf = _seam_confidence(sp, boundaries, k)
        boxes.append((tx0, y0, tx1, y1, conf))
    return boxes


def segment_tubes(
    frame: np.ndarray,
    *,
    axis: str = "horizontal",
    expected_count: Optional[int] = None,
    min_tubes: int = 1,
    max_tubes: int = MAX_TUBES_HARD,
    area: Optional[InspectArea] = None,
    valid: Optional[np.ndarray] = None,
) -> List[TubeROI]:
    """다중 튜브 프레임 → 튜브별 TubeROI 리스트(축 순서 index 1..N).

    axis: "horizontal"(튜브가 가로로 누워 세로로 쌓임) | "vertical"(반대).
    expected_count: 알려진 튜브 개수. 주어지면 그 수에 맞춰 seam 보정.
    min_tubes/max_tubes: 자동/보정 개수의 하한·상한(하드 상한 MAX_TUBES_HARD,
        기본 64 · 환경변수 AIVIS_MAX_TUBES 로 조정).
    area: 검사 영역(preprocess/area.py). 주면 **그 안에서만** 튜브를 나눈다 — 영역 밖
        물체(옆 다발·공구·손)는 개수에도 경계에도 끼지 않는다. 좌표는 원본 프레임 기준.
    결정적. 빈/전경없음 → 빈 리스트.
    """
    if frame is None or frame.ndim != 3:
        raise ValueError("segment_tubes: BGR 3채널 이미지가 필요하다")
    rect = area_rect(area, frame)
    if rect is not None:
        inner = segment_tubes(rect.crop(frame), axis=axis, expected_count=expected_count,
                              min_tubes=min_tubes, max_tubes=max_tubes,
                              valid=rect.crop(valid) if valid is not None else None)
        return [TubeROI(index=t.index, x0=t.x0 + rect.x0, y0=t.y0 + rect.y0,
                        x1=t.x1 + rect.x0, y1=t.y1 + rect.y0, confidence=t.confidence)
                for t in inner]
    if axis == "auto":
        axis = resolve_axis(frame)
    if axis not in ("horizontal", "vertical"):
        raise ValueError("segment_tubes: axis 는 horizontal|vertical|auto")
    max_tubes = int(max(1, min(MAX_TUBES_HARD, max_tubes)))
    min_tubes = int(max(1, min(max_tubes, min_tubes)))
    if expected_count is not None and expected_count < 1:
        expected_count = None

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if axis == "vertical":
        # 전치해 세로 튜브를 가로 튜브 문제로 환원.
        boxes_t = _segment_axis(gray.T.copy(), expected_count, min_tubes, max_tubes,
                                valid=valid.T.copy() if valid is not None else None)
        # 전치 좌표(x',y') → 원본(x=y', y=x'): (y0,x0,y1,x1)로 스왑.
        boxes = [(y0, x0, y1, x1, c) for (x0, y0, x1, y1, c) in boxes_t]
    else:
        boxes = _segment_axis(gray, expected_count, min_tubes, max_tubes, valid=valid)

    return [
        TubeROI(index=i + 1, x0=b[0], y0=b[1], x1=b[2], y1=b[3], confidence=round(b[4], 4))
        for i, b in enumerate(boxes)
    ]


def resolve_axis(frame: np.ndarray) -> str:
    """튜브가 가로로 누웠나 세로로 섰나 — 대표 기울기가 ±45° 안이면 가로(튜브가 없으면 가로)."""
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    ang = dominant_angle(gray)
    return "vertical" if ang is not None and abs(ang) > 45.0 else "horizontal"


__all__ = ["TubeROI", "segment_tubes", "resolve_axis", "MAX_TUBES_HARD"]
