"""제품(튜브) 전경 찾기 — 배경이 밝아도, 무늬가 있어도, 튜브가 어느 방향이어도 (2026-10-21).

종전 분할은 '밝은 튜브 · 어두운 배경'(Otsu 로 밝은 쪽이 제품)을 가정했다. 현장 사진(밝은 회색
얼룩무늬 바닥 위 알루미늄 튜브 4개)에서는 **바닥이 더 밝아** 바닥 전체를 제품으로 잡았고
(화면의 46~81%), 다발 분할은 화면을 기대 개수만큼 띠로 잘라 '4개' 를 돌려줬다 — 하나도 못 찾았다.

여기서는 밝기가 아니라 **주변 배경과 다른 정도**로 찾는다.
1) 큰 미디언 필터로 배경(바닥·그림자의 완만한 변화)을 추정한다 — 튜브는 필터 창보다 가늘어서 빠진다.
2) |영상 − 배경| 은 금속 튜브의 반사 띠(밝음)와 가장자리(어두움)를 **극성과 무관하게** 드러낸다.
   바닥 얼룩·타일 이음매는 작거나 가늘어 형태 연산으로 지운다.
3) 연결 요소마다 회전 사각형(minAreaRect)으로 길이·폭·각도를 잰다.

배경이 어두우면(밝은 쪽이 화면의 절반 미만) 종전 Otsu 를 그대로 쓴다 — 기존 동작과 시험이 바뀌지 않는다.
현장 권장은 여전히 **무광 어두운 배경**이다(부록 A.3) — 대비가 클수록 끝단이 정확하다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np

#: (종전 규칙의 값 — 다른 곳에서 참조할 수 있어 남겨 둔다)
LIGHT_BG_FRACTION = 0.5
#: 화면 테두리 밝기 ÷ 가장 밝은 곳(99.5%)이 이 값 이상이면 밝은 배경.
LIGHT_RING_RATIO = 0.4
#: 배경 추정·형태 연산은 긴 변을 이 크기로 줄여 한다(속도 · 크기에 무관한 커널).
_WORK_SIDE = 1000


def _odd(n: float) -> int:
    n = max(3, int(round(n)))
    return n if n % 2 else n + 1


def light_background(gray: np.ndarray) -> bool:
    """밝은 배경인가 — 화면 테두리(배경이 보이는 곳)의 밝기가 화면에서 가장 밝은 곳의 40% 이상이면.

    종전(2026-10-21)에는 'Otsu 의 밝은 쪽이 화면 절반을 넘으면' 이었다. 직접 만든 장면 평가(2026-10-24)에서
    두 가지로 틀렸다 — ① 어두운 바닥에 큰 다발을 올리면 밝은 튜브가 화면 절반을 넘어 밝은 배경으로,
    ② 조명이 한쪽으로 기운 밝은 바닥은 Otsu 가 바닥의 어두운 쪽·밝은 쪽을 갈라 어두운 배경으로 봤다.
    배경은 화면 가장자리에 보이고(다발이 테두리를 다 덮는 일은 없다), 무광 검정 바닥은 반사 띠(가장 밝은
    곳)의 1/3 도 안 된다. 노출과 무관하게 비율로 본다.
    """
    if float(gray.max()) - float(gray.min()) < 20.0:
        return False  # 평탄한 화면(빈 장면) — 판단할 것이 없다. 종전 경로 그대로.
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    h, w = blur.shape[:2]
    k = max(2, int(0.04 * min(h, w)))
    ring = np.concatenate([blur[:k].ravel(), blur[-k:].ravel(), blur[k:-k, :k].ravel(), blur[k:-k, -k:].ravel()])
    top = float(np.percentile(blur, 99.5))
    return float(np.median(ring)) >= LIGHT_RING_RATIO * max(top, 1.0)


def _otsu_mask(gray: np.ndarray) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _t, binm = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binm


def contrast_mask(gray: np.ndarray) -> np.ndarray:
    """배경과 다른 곳(극성 무관) → 0/255 마스크, 원본 크기. 밝은·무늬 배경용."""
    h, w = gray.shape[:2]
    s = min(1.0, _WORK_SIDE / float(max(h, w)))
    g = gray if s == 1.0 else cv2.resize(gray, (max(1, int(w * s)), max(1, int(h * s))),
                                         interpolation=cv2.INTER_AREA)
    gh, gw = g.shape[:2]
    k = min(_odd(min(gh, gw) / 8), 255)  # 튜브 폭의 2~3배 — 튜브는 배경 추정에서 빠진다
    bg = cv2.medianBlur(g, k)
    diff = cv2.GaussianBlur(cv2.absdiff(g, bg), (5, 5), 0)
    _t, binm = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    short = min(gh, gw)
    close_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(short / 50),) * 2)
    open_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(short / 70),) * 2)
    binm = cv2.morphologyEx(binm, cv2.MORPH_CLOSE, close_k)   # 반사 띠·가장자리 사이를 메운다
    binm = cv2.morphologyEx(binm, cv2.MORPH_OPEN, open_k)     # 바닥 얼룩·타일 이음매를 지운다
    if s != 1.0:
        binm = cv2.resize(binm, (w, h), interpolation=cv2.INTER_NEAREST)
    return binm


def tube_mask(gray: np.ndarray, light: Optional[bool] = None) -> np.ndarray:
    """튜브 전경 마스크. 어두운 배경은 종전 Otsu(변경 없음), 밝은 배경은 contrast_mask.

    light: 배경이 밝은가. **전체 프레임**에서 정해 넘긴다 — 튜브 한 개를 꼭 맞게 자른 영상은
    튜브가 화면을 거의 채워 '밝은 쪽이 절반 넘음' 이 되므로 거기서 판단하면 틀린다. None 이면 이
    영상에서 판단(전체 프레임일 때만).
    """
    if light is None:
        light = light_background(gray)
    return contrast_mask(gray) if light else _otsu_mask(gray)


@dataclass(frozen=True)
class Blob:
    """전경 연결 요소 하나의 회전 사각형. angle: 긴 축의 기울기(도, 가로 0 · 세로 ±90)."""

    cx: float
    cy: float
    length: float
    width: float
    angle: float
    area: int
    label: int = 0          # 연결 요소 번호(find_blobs 가 준 라벨 영상에서)
    x0: int = 0             # 축 정렬 상자(원본 좌표)
    y0: int = 0
    x1: int = 0
    y1: int = 0

    @property
    def elongation(self) -> float:
        return self.length / max(self.width, 1.0)


def label_blobs(mask: np.ndarray, *, min_area_frac: float = 0.0005):
    """(라벨 영상, 덩어리 목록) — 면적 큰 순. 라벨로 덩어리 하나만의 마스크를 만들 수 있다."""
    h, w = mask.shape[:2]
    n, lab, st, _c = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    min_area = max(50, int(min_area_frac * h * w))
    out: List[Blob] = []
    for i in range(1, n):
        area = int(st[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        ys, xs = np.nonzero(lab == i)
        (cx, cy), (rw, rh), ang = cv2.minAreaRect(np.column_stack([xs, ys]).astype(np.float32))
        if rw >= rh:
            length, width, a = rw, rh, ang
        else:
            length, width, a = rh, rw, ang + 90.0
        a = ((a + 90.0) % 180.0) - 90.0  # (-90, 90]
        x, y, bw, bh = (int(st[i, k]) for k in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP,
                                               cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
        out.append(Blob(float(cx), float(cy), float(length), float(width), float(a), area,
                        label=i, x0=x, y0=y, x1=x + bw, y1=y + bh))
    out.sort(key=lambda b: -b.area)
    return lab, out


def find_blobs(mask: np.ndarray, *, min_area_frac: float = 0.0005) -> List[Blob]:
    """길쭉한 전경 덩어리(튜브 또는 붙은 다발) 목록 — 면적 큰 순."""
    return label_blobs(mask, min_area_frac=min_area_frac)[1]


def _axis_mean(blobs: List[Blob]) -> float:
    """축 방향(180° 주기)의 면적 가중 원형 평균 — 89° 와 −89° 가 0° 로 평균되지 않게 각도를 두 배로."""
    ws = np.array([b.area for b in blobs], dtype=np.float64)
    th = np.radians([2.0 * b.angle for b in blobs])
    a = 0.5 * np.degrees(np.arctan2((ws * np.sin(th)).sum(), (ws * np.cos(th)).sum()))
    return float(((a + 90.0) % 180.0) - 90.0)


def angle_gap(a: float, b: float) -> float:
    """두 축 방향의 차(0~90도)."""
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def select_tubes(blobs: List[Blob]) -> List[Blob]:
    """튜브로 볼 만한 덩어리만 — 길쭉하고(길이/폭 ≥ 3), 가장 긴 것의 1/4 이상, 대표 방향과 25° 안,
    폭이 다른 튜브의 절반 이상(화면 가장자리·타일 이음매 같은 가는 선은 빠진다)."""
    cand = [b for b in blobs if b.elongation >= 3.0]
    if not cand:
        return []
    longest = max(b.length for b in cand)
    cand = [b for b in cand if b.length >= 0.25 * longest]
    dom = _axis_mean(cand[: max(1, min(len(cand), 8))])
    cand = [b for b in cand if angle_gap(b.angle, dom) <= 25.0]
    if not cand:
        return []
    medw = float(np.median([b.width for b in cand]))
    return [b for b in cand if b.width >= 0.5 * medw]


def tube_blobs(gray: np.ndarray) -> List[Blob]:
    """튜브(또는 붙은 다발) 덩어리 목록."""
    return select_tubes(find_blobs(tube_mask(gray)))


def dominant_angle(gray: np.ndarray) -> Optional[float]:
    """튜브 긴 축의 대표 기울기(도). 가로 0, 세로 ±90. 튜브가 없으면 None."""
    blobs = tube_blobs(gray)
    return _axis_mean(blobs) if blobs else None


__all__ = ["Blob", "angle_gap", "contrast_mask", "dominant_angle", "find_blobs", "label_blobs",
           "light_background", "select_tubes", "tube_blobs", "tube_mask"]
