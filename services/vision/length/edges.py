"""끝단 에지 **직선 적합** — 기울기를 재서 지우고, 행 수만큼 노이즈를 줄인다.

종전 방식(`measure.py` 의 세로 평균 프로파일)에는 **계통오차가 하나 숨어 있다.**

튜브가 ROI 축에서 θ 만큼 기울면 절단면도 수직에서 θ 만큼 기운다. 세로로 평균한
프로파일에서 재는 것은 두 기운 직선 사이의 **수평** 거리이고, 그 값은 참값의
`1/cosθ` 배다. 길이에 비례하므로 그냥 커진다.

| 기울기 θ | 250mm 제품의 과대측정 |
|---|---|
| 0.5° | +0.010mm |
| 1° | +0.038mm |
| 2° | +0.152mm |
| 3° | +0.343mm |

±0.1mm(폭 0.2mm) 공차에서 **2° 면 혼자 공차를 다 먹는다.** 컨베이어 위 다발이
2° 안쪽으로 가지런할 것이라고 기대할 수는 없다.

여기서는 행마다 서브픽셀 끝단을 구해 **직선을 적합**한다. 그러면
  1. 기울기 θ 가 측정값으로 나오므로 **수직거리**를 쓸 수 있다(계통오차 제거),
  2. 끝단 위치가 N행 평균이 되어 노이즈가 1/√N 로 줄고,
  3. 적합 잔차로 "이 끝단이 정말 직선인가"(버·찌그러짐)를 알 수 있다.

세로 평균도 1·2 중 2는 이미 얻고 있었다. 빠져 있던 것은 1과 3이다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

#: 행별 끝단을 찾을 때 거친 추정 주변으로 볼 창(px). 기울기가 커도 담기도록
#: 넉넉히 두되, 옆 구조물을 끌어들이지 않을 만큼만.
SEARCH_HALF_WIDTH_PX = 24

#: 적합에 쓰기 위한 행별 최소 에지 강도(그래디언트 절댓값).
MIN_ROW_GRAD = 2.0

#: MAD 기반 이상치 제거 임계(중앙값 절대편차의 배수).
OUTLIER_MAD_K = 3.0

# --- 적합 신뢰 게이트 -------------------------------------------------------
# 약한 적합이 측정을 덮어쓰면 보정이 아니라 **새 오차**다. 아래를 모두
# 통과할 때만 기울기 보정을 적용하고, 하나라도 걸리면 종전 경로로 떨어진다.

#: 양쪽 끝단이 본 행 수가 이 비율만큼은 서로 맞아야 한다. 한쪽만 많이 봤다면
#: 한쪽이 끝단이 아닌 것을 보고 있다는 뜻이다.
#: (ROI 높이 기준으로 재지 않는다 — 튜브는 ROI 높이의 일부만 차지한다.)
MIN_ROW_AGREEMENT = 0.5
#: 절대 하한. 몇 행짜리 적합의 기울기는 기울기가 아니라 노이즈다.
MIN_ROWS_ABS = 8
#: 컨베이어에 놓인 튜브가 이보다 기울 수는 없다 — 넘으면 오검출이다.
MAX_TILT_DEG = 10.0
#: 두 끝단은 같은 축에 수직이므로 평행해야 한다. 어긋나면 한쪽이 틀렸다.
MAX_PARALLEL_DEG = 2.0
#: 직선 적합 잔차 상한(px). 크면 끝단이 직선이 아니다.
MAX_RESIDUAL_PX = 2.0


@dataclass(frozen=True)
class EdgeLine:
    """한쪽 끝단에 적합한 직선. ROI 로컬 좌표계.

    직선은 `x = intercept + slope * (y - y_center)` 로 둔다. y 를 중심화하면
    intercept 가 "ROI 세로 중앙에서의 x" 가 되어 해석이 쉽고, slope 와의 상관도
    사라져 적합이 안정적이다.
    """

    intercept: float
    """ROI 세로 중앙에서의 서브픽셀 x."""
    slope: float
    """dx/dy. 수직선이면 0. 기울기 θ(수직 기준)에 대해 slope = tan θ."""
    n_rows: int
    """적합에 실제로 쓰인 행 수. 노이즈가 1/√n_rows 로 줄어든다."""
    residual_px: float
    """적합 잔차 RMS. 크면 끝단이 직선이 아니다(버·깨짐·검출실패 혼입)."""

    @property
    def tilt_deg(self) -> float:
        return math.degrees(math.atan(self.slope))

    def x_at(self, dy: float) -> float:
        return self.intercept + self.slope * dy


@dataclass(frozen=True)
class EdgeLineFit:
    left: EdgeLine
    right: EdgeLine

    @property
    def mean_slope(self) -> float:
        """두 끝단은 같은 축에 수직이므로 평행해야 한다 — 평균을 쓴다."""
        return 0.5 * (self.left.slope + self.right.slope)

    @property
    def horizontal_px(self) -> float:
        """세로 중앙에서의 수평 거리. 종전 방식이 쓰던 값(기울기 미보정)."""
        return self.right.intercept - self.left.intercept

    @property
    def perpendicular_px(self) -> float:
        """두 평행선 사이의 **수직거리** = 참 길이.

        x = a + b·y 꼴 두 직선의 거리는 |Δa| / √(1+b²) 다. b = tanθ 이므로
        수평거리 × cosθ 와 같다.
        """
        b = self.mean_slope
        return self.horizontal_px / math.sqrt(1.0 + b * b)

    @property
    def tilt_deg(self) -> float:
        return math.degrees(math.atan(self.mean_slope))

    @property
    def parallelism_deg(self) -> float:
        """두 끝단 직선이 어긋난 각도. 크면 절단이 비스듬하거나 검출이 틀렸다."""
        return abs(self.left.tilt_deg - self.right.tilt_deg)

    def reject_reason(self, roi_height: int = 0) -> Optional[str]:
        """이 적합을 믿어도 되는가. None 이면 통과, 아니면 사유 문자열.

        사유를 문자열로 돌려주는 것은 로그에 남기기 위해서다. 조용히 폴백하면
        현장에서 "왜 기울기 보정이 안 먹지" 를 알아낼 방법이 없다.
        """
        nl, nr = self.left.n_rows, self.right.n_rows
        if min(nl, nr) < MIN_ROWS_ABS:
            return f"행 부족(L{nl}/R{nr} < {MIN_ROWS_ABS})"
        if min(nl, nr) < MIN_ROW_AGREEMENT * max(nl, nr):
            return f"양끝 행수 불일치(L{nl}/R{nr})"
        if abs(self.tilt_deg) > MAX_TILT_DEG:
            return f"기울기 과대({self.tilt_deg:.1f}° > {MAX_TILT_DEG}°)"
        if self.parallelism_deg > MAX_PARALLEL_DEG:
            return f"끝단 비평행({self.parallelism_deg:.1f}°)"
        worst = max(self.left.residual_px, self.right.residual_px)
        if worst > MAX_RESIDUAL_PX:
            return f"적합 잔차 과대({worst:.2f}px)"
        return None


def _row_subpixel(grad_row: np.ndarray, idx: int) -> Optional[float]:
    """3점 포물선 정점. 경계·평탄이면 None(그 행은 버린다)."""
    if idx <= 0 or idx >= grad_row.size - 1:
        return None
    y0, y1, y2 = float(grad_row[idx - 1]), float(grad_row[idx]), float(grad_row[idx + 1])
    denom = y0 - 2.0 * y1 + y2
    if abs(denom) < 1e-9:
        return float(idx)
    delta = 0.5 * (y0 - y2) / denom
    if abs(delta) > 1.0:
        return float(idx)
    return float(idx) + delta


def _fit_line(ys: np.ndarray, xs: np.ndarray, y_center: float) -> Optional[EdgeLine]:
    """중심화 최소제곱 + MAD 이상치 제거 1회.

    이상치 제거를 한 번만 도는 것은 의도다 — 반복하면 결정성이 떨어지고(§5 M5
    DoD: 동일 입력 → 동일 출력) 시간도 든다. 한 번으로 버·반사 몇 행은 충분히
    걸러진다.
    """
    if ys.size < 3:
        return None
    dy = ys.astype(np.float64) - y_center

    def solve(dy_: np.ndarray, xs_: np.ndarray) -> Optional[Tuple[float, float]]:
        n = dy_.size
        if n < 3:
            return None
        sxx = float(np.dot(dy_, dy_))
        if sxx < 1e-9:          # 행이 한 줄에 몰렸다 → 기울기를 못 푼다
            return float(np.mean(xs_)), 0.0
        slope = float(np.dot(dy_, xs_ - xs_.mean()) / sxx)
        intercept = float(xs_.mean() - slope * dy_.mean())
        return intercept, slope

    first = solve(dy, xs)
    if first is None:
        return None
    a, b = first
    resid = xs - (a + b * dy)
    mad = float(np.median(np.abs(resid - np.median(resid))))
    if mad > 1e-9:
        keep = np.abs(resid - np.median(resid)) <= OUTLIER_MAD_K * 1.4826 * mad
        if int(keep.sum()) >= 3:
            dy, xs = dy[keep], xs[keep]
            second = solve(dy, xs)
            if second is not None:
                a, b = second
                resid = xs - (a + b * dy)
    rms = float(np.sqrt(np.mean(resid * resid))) if resid.size else 0.0
    return EdgeLine(intercept=a, slope=b, n_rows=int(xs.size), residual_px=rms)


def fit_edge_lines(
    gray_roi: np.ndarray,
    coarse_left: float,
    coarse_right: float,
    *,
    half_width: int = SEARCH_HALF_WIDTH_PX,
    min_row_grad: float = MIN_ROW_GRAD,
) -> Optional[EdgeLineFit]:
    """거친 끝단 추정 주변에서 행별 서브픽셀 에지를 모아 직선 두 개를 적합.

    coarse_left/right 는 세로 평균 프로파일로 얻은 값을 넣는다(기존 경로 재사용).
    실패(유효 행 부족)하면 None — 호출자는 종전 방식으로 떨어진다.
    """
    if gray_roi is None or gray_roi.ndim != 2:
        return None
    h, w = gray_roi.shape
    if h < 3 or w < 5:
        return None

    g = gray_roi.astype(np.float32)
    # 행 전체의 수평 그래디언트를 한 번에. 행마다 돌면 파이에서 느리다.
    grad = np.gradient(g, axis=1)
    ys = np.arange(h)
    y_center = (h - 1) / 2.0

    def side(coarse: float, rising: bool) -> Optional[EdgeLine]:
        c = int(round(coarse))
        lo = max(0, c - half_width)
        hi = min(w, c + half_width + 1)
        if hi - lo < 3:
            return None
        win = grad[:, lo:hi]
        # 좌측 끝단은 상승 에지(+), 우측은 하강 에지(-).
        signed = win if rising else -win
        idx = np.argmax(signed, axis=1)
        peak = signed[ys, idx]
        ok = peak >= min_row_grad
        if int(ok.sum()) < 3:
            return None
        xs = np.empty(h, dtype=np.float64)
        good = np.zeros(h, dtype=bool)
        for r in np.nonzero(ok)[0]:
            sub = _row_subpixel(signed[r], int(idx[r]))
            if sub is None:
                continue
            xs[r] = lo + sub
            good[r] = True
        if int(good.sum()) < 3:
            return None
        return _fit_line(ys[good], xs[good], y_center)

    # 거친 추정이 ROI 경계에 붙어 있으면 둘 중 하나다.
    #   (a) 크롭이 튜브를 꽉 채워 끝단이 시야 밖 — 적합하면 측정 구간을 안쪽으로
    #       잘라먹으므로 손대면 안 된다.
    #   (b) 기울기가 커서 세로평균 프로파일의 에지가 희석돼 거친 추정이 경계
    #       아티팩트로 튄 것 — 이때는 오히려 적합이 필요하다.
    # 둘을 가르는 것은 "행별로 보면 진짜 에지가 있는가" 다. 행별 탐색은 세로
    # 평균과 달리 기울기에 희석되지 않으므로 (b)에서는 멀쩡히 찾는다.
    edge_margin = 2
    degenerate = coarse_left <= edge_margin or coarse_right >= w - 1 - edge_margin
    if degenerate:
        seed = _row_median_seed(grad, min_row_grad)
        if seed is None:
            return None                      # (a) — 종전 경로 유지
        coarse_left, coarse_right = seed

    left = side(coarse_left, rising=True)
    right = side(coarse_right, rising=False)
    if left is None or right is None:
        return None
    if right.intercept <= left.intercept:
        return None
    return EdgeLineFit(left=left, right=right)


def _row_median_seed(
    grad: np.ndarray, min_row_grad: float
) -> Optional[Tuple[float, float]]:
    """행별 최강 상승/하강 에지의 **중앙값** 으로 시드를 잡는다.

    세로평균 프로파일과 달리 기울기에 희석되지 않는다. 중앙값이라 몇 행이
    엉뚱해도 버틴다. 에지가 아예 없으면(튜브가 ROI 를 꽉 채움) None.
    """
    h, w = grad.shape
    if w < 5:
        return None
    # 경계 2px 은 미분이 한쪽차분이라 불안정하다 — 시드 탐색에서 뺀다.
    inner = grad[:, 2 : w - 2]
    if inner.shape[1] < 3:
        return None
    li = np.argmax(inner, axis=1)
    ri = np.argmin(inner, axis=1)
    lp = inner[np.arange(h), li]
    rp = -inner[np.arange(h), ri]
    ok = (lp >= min_row_grad) & (rp >= min_row_grad) & (ri > li)
    if int(ok.sum()) < MIN_ROWS_ABS:
        return None
    return float(np.median(li[ok]) + 2), float(np.median(ri[ok]) + 2)


def tilt_overmeasure_mm(length_mm: float, tilt_deg: float) -> float:
    """기울기를 보정하지 않았을 때의 과대측정량(mm). 문서·테스트용."""
    return length_mm * (1.0 / math.cos(math.radians(tilt_deg)) - 1.0)
