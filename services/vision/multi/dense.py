"""붙어 누운 다발 — 개수와 튜브별 위치·끝 (밝은 받침·트레이 위, 원근이 있어도) (2026-10-21).

현장 사진(밝은 회색 트레이에 튜브 19개를 붙여 눕힘, 휴대폰으로 여러 각도)에서 종전 분할은
0~5개를 찾았다. 튜브가 서로 붙어 한 덩어리가 되면 '배경과 다른 곳' 마스크(foreground.py)가
덩어리 전체를 배경으로 보고(미디언 창보다 넓다), 남는 것은 트레이 테두리뿐이었다.

여기서는 덩어리를 **줄무늬**로 본다.

1) 방향 — 모서리 방향 분포의 봉우리(axis_angle). 튜브 경계가 압도적으로 많다.
2) 다발 위치 — 축 방향으로 흐린 뒤 축을 가로지르는 기울기의 밀도(줄무늬 에너지).
3) 원근 펴기 — 실제로 평행한 튜브는 사진에서 한 소실점으로 모인다. 구조 텐서로 잰 국소 기울기에
   m = a + b·(u − yc) (모든 선이 한 점을 지나는 꼴)를 맞추고, 그 선들이 가로줄이 되게 편다.
4) 개수 — 편 영상에서 축 방향 중앙값 프로파일은 튜브마다 한 주기다. 튜브 사이 틈(어두운 골)으로
   간격을 재되 원근 때문에 간격이 위치에 따라 바뀌므로 간격을 직선 p(y) 로 두고 그 격자를 골에 맞춘다.
   다발의 바깥 경계는 **표면 결(국소 표준편차)** 로 정한다 — 금속은 반사 띠·음영으로 결이 세고
   트레이 플라스틱은 매끈하다(현장 사진 20개 경계에서 튜브 ≥ 0.76, 트레이 ≤ 0.45, 다발 안 중앙값 대비).
   경계 안에 중심이 든 격자 칸 수가 개수다. 바깥 칸 중심이 경계에 걸쳐 있으면(여유 < 0.12 피치) 또는
   '골 사이 간격을 국소 피치로 반올림' 해 센 값과 2개 이상 다르면 **불확실**로 표시한다(재확인 —
   조용히 고르지 않는다).
5) 끝 — 튜브 띠마다 축을 가로지르는 기울기(원통 음영)가 끊기는 곳. 끝의 트레이 벽·리브는 축에 수직이라
   이 기울기를 만들지 않는다. 이웃 튜브와 동떨어진 끝은 이웃으로 맞춘 값을 출발점으로 쓴다.
   정밀한 끝은 파이프라인이 튜브 띠(ribbon) 영상에서 서브픽셀로 다시 잰다.

모두 결정적이며 학습이 필요 없다. 어두운 배경(현장 권장)에서는 종전 분할을 쓴다 — 이 모듈은
밝은 배경에서 종전 분할이 덩어리를 못 나눌 때만 쓰인다(batch.inspect_batch).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

#: 격자 칸 중심이 결 경계에서 이만큼(피치 비) 안쪽이어야 튜브로 센다 — 결 경계는 실제 끝보다 약간 밖이다.
EDGE_SHRINK = 0.25
#: 결 경계: 다발 안 중앙값의 이 비율 아래가 p/3 넘게 이어지면 다발 밖.
EDGE_TEX_RATIO = 0.5
#: 끝 찾기: 축을 가로지르는 기울기가 다발 안 기준의 이 비율 아래로 p/2 넘게 이어지면 끝.
END_GRAD_RATIO = 0.3
#: 경계 여유(피치 비)가 이보다 작으면 개수를 '불확실'로 — 바깥 칸이 튜브인지 트레이인지 반반이다.
MARGIN_MIN = 0.12


def _odd(n: float) -> int:
    n = max(3, int(round(n)))
    return n if n % 2 else n + 1


def _gsmooth(p: np.ndarray, sigma: float) -> np.ndarray:
    if sigma < 0.5:
        return np.asarray(p, dtype=np.float64).copy()
    r = int(3 * sigma) + 1
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    return np.convolve(np.pad(np.asarray(p, dtype=np.float64), r, mode="edge"), k, mode="valid")


def _minima(p: np.ndarray) -> List[int]:
    return [i for i in range(1, len(p) - 1) if p[i] <= p[i - 1] and p[i] < p[i + 1]]


def axis_angle(gray: np.ndarray, work: int = 800) -> Optional[float]:
    """튜브 긴 축의 기울기(도, 가로 0 · 세로 ±90, foreground.Blob 과 같은 규약) — 모서리 방향 분포의 봉우리.

    붙은 다발처럼 덩어리를 못 나누는 장면에서도 튜브 경계는 화면에서 가장 많은 같은 방향의 모서리다.
    평탄한 화면이면 None.
    """
    h, w = gray.shape[:2]
    s = min(1.0, work / float(max(h, w)))
    g = gray if s == 1.0 else cv2.resize(gray, (max(1, int(w * s)), max(1, int(h * s))),
                                         interpolation=cv2.INTER_AREA)
    g = cv2.GaussianBlur(g.astype(np.float32), (0, 0), 1.0)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.hypot(gx, gy)
    if float(mag.max()) < 1e-3:
        return None
    sel = mag > np.percentile(mag, 90)
    if int(sel.sum()) < 50:
        return None
    ang = (np.degrees(np.arctan2(gy[sel], gx[sel])) + 90.0) % 180.0   # 모서리(=축) 방향
    hist, _ = np.histogram(ang, bins=180, range=(0, 180), weights=mag[sel])
    hs = np.convolve(np.r_[hist[-3:], hist, hist[:3]], np.ones(5) / 5, mode="same")[3:-3]
    k = int(np.argmax(hs))
    idx = np.arange(k - 3, k + 4)
    wts = hs[idx % 180]
    a = float((idx + 0.5) @ wts / max(float(wts.sum()), 1e-9))
    return float(((a + 90.0) % 180.0) - 90.0)


def _stripe_box(g: np.ndarray, valid: Optional[np.ndarray] = None) -> Optional[Tuple[int, int, int, int]]:
    """줄무늬 에너지(축 방향으로 흐린 뒤 축을 가로지르는 기울기의 밀도)가 센 가장 큰 덩어리."""
    h, w = g.shape
    d = max(h, w)
    S = cv2.blur(g, (_odd(0.03 * d), 1))
    gy = np.abs(cv2.Sobel(S, cv2.CV_32F, 0, 1, ksize=3))
    if valid is not None and valid.shape == gy.shape:
        gy[valid == 0] = 0
    E = cv2.blur(gy, (_odd(0.04 * d),) * 2)
    e8 = cv2.normalize(E, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _t, m = cv2.threshold(e8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    n, _lab, st, _c = cv2.connectedComponentsWithStats(m)
    if n < 2:
        return None
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    x, y, bw, bh = (int(v) for v in st[i, :4])
    if bw * bh < 0.01 * h * w or bw < 20 or bh < 20:
        return None
    return x, y, x + bw, y + bh


def _spec_pitch(seg: np.ndarray, lo: float, hi: float) -> Tuple[Optional[float], float]:
    if len(seg) < 8 or hi <= lo:
        return None, 0.0
    hp = seg - _gsmooth(seg, max(3.0, len(seg) / 6))
    hp = hp * np.hanning(len(hp))
    n = 1 << int(np.ceil(np.log2(len(hp) * 8)))
    F = np.abs(np.fft.rfft(hp, n))
    fr = np.fft.rfftfreq(n)
    ok = (fr >= 1.0 / hi) & (fr <= 1.0 / lo)
    if not ok.any():
        return None, 0.0
    i = int(np.argmax(np.where(ok, F, 0)))
    return (1.0 / fr[i] if fr[i] > 0 else None), float(F[i])


def _global_pitch(seg: np.ndarray) -> Optional[float]:
    """주기(튜브 간격). 튜브 안 무늬가 2배음을 만들므로 절반 주파수가 충분히 세면 그쪽."""
    p, _ = _spec_pitch(seg, 8, len(seg) / 3)
    if p is None:
        return None
    p2, a2 = _spec_pitch(seg, 1.7 * p, 2.3 * p)
    _p1, a1 = _spec_pitch(seg, 0.9 * p, 1.1 * p)
    if p2 and a2 >= 0.6 * a1 and p2 <= len(seg) / 3:
        p = p2
    return p


def _hinted_pitch(seg: np.ndarray, hint: float) -> Optional[float]:
    p, _ = _spec_pitch(seg, 0.8 * hint, 1.3 * hint)
    return p


def _fan_fit(g: np.ndarray, box, p_guess: float) -> Tuple[float, float, float, float]:
    """튜브 축 기울기장 m = a + b·(u − yc). 모든 튜브가 한 소실점을 지나는 꼴(원근)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    sub = g[y0:y1, x0:x1]
    gx = cv2.Sobel(sub, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(sub, cv2.CV_32F, 0, 1, ksize=3)
    k = _odd(max(5, p_guess))
    jxx = cv2.blur(gx * gx, (k, k))
    jyy = cv2.blur(gy * gy, (k, k))
    jxy = cv2.blur(gx * gy, (k, k))
    root = np.sqrt((jyy - jxx) ** 2 + 4 * jxy ** 2)
    tr = jxx + jyy + 1e-6
    coh = root / tr
    m = -2 * jxy / (jyy - jxx + root + 1e-6)          # 접선(작은 고유값 쪽) 기울기 dy/dx
    step = max(1, int(p_guess // 2))
    sel = (coh > 0.6) & (tr > np.percentile(tr, 50))
    sel[:, : int(0.15 * w)] = False
    sel[:, int(0.85 * w):] = False
    sel = sel[::step, ::step]
    ys, xs = np.mgrid[0:h:step, 0:w:step]
    X = (xs[sel] + x0).astype(np.float64)
    Y = (ys[sel] + y0).astype(np.float64)
    M = m[::step, ::step][sel].astype(np.float64)
    Wt = tr[::step, ::step][sel].astype(np.float64)
    xc, yc = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
    if M.size < 10:
        return 0.0, 0.0, xc, yc
    a, b = float(np.median(M)), 0.0
    for _ in range(4):
        U = Y - (a + b * (Y - yc)) * (X - xc)          # 이 점을 지나는 선의 xc 에서의 높이
        A = np.column_stack([np.ones_like(U), U - yc])
        r = M - A @ np.array([a, b])
        sc = 1.4826 * float(np.median(np.abs(r))) + 1e-6
        wt = Wt / (1 + (r / (2 * sc)) ** 2)
        sol, *_ = np.linalg.lstsq(A * np.sqrt(wt)[:, None], M * np.sqrt(wt), rcond=None)
        a, b = float(sol[0]), float(sol[1])
    return a, b, xc, yc


def _rectify(g: np.ndarray, a: float, b: float, xc: float, yc: float) -> np.ndarray:
    h, w = g.shape
    u = np.arange(h, dtype=np.float32)[:, None]
    x = np.arange(w, dtype=np.float32)[None, :]
    mapy = (u + (a + b * (u - yc)) * (x - xc)).astype(np.float32)
    mapx = np.broadcast_to(x, (h, w)).astype(np.float32)
    return cv2.remap(g, mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _texture(R: np.ndarray, p: float) -> np.ndarray:
    k = max(3, int(round(p / 4)) | 1)
    m = cv2.blur(R, (k, k))
    m2 = cv2.blur(R * R, (k, k))
    return np.sqrt(np.maximum(m2 - m * m, 0))


def _nms_seams(Q: np.ndarray, p: float, lo: float, hi: float) -> List[int]:
    """깊은 골부터 남긴다(간격 0.6 피치) — 튜브 가운데의 옅은 골은 양옆 틈에 밀려 빠진다."""
    sp = _gsmooth(Q, p / 8)
    mins = [m for m in _minima(sp) if lo <= m <= hi]
    keep: List[int] = []
    for m in sorted(mins, key=lambda i: sp[i]):
        if all(abs(m - q) >= 0.6 * p for q in keep):
            keep.append(m)
    return sorted(keep)


def _pitch_line(seams: List[int], p: float) -> Tuple[float, float]:
    """골 간격 → 직선 p(y) = k·y + c (원근: 가까운 쪽 튜브가 굵어 보인다). 가장자리 간격은 반쪽일 수 있어 뺀다."""
    s = np.array(seams, float)
    if len(s) < 5:
        return 0.0, p
    mid, d = 0.5 * (s[1:] + s[:-1]), np.diff(s)
    ok = (d >= 0.7 * p) & (d <= 1.4 * p)
    mid, d = mid[ok][1:-1], d[ok][1:-1]
    if len(d) < 3:
        return 0.0, float(np.median(np.diff(s)))
    k, c = np.polyfit(mid, d, 1)
    for _ in range(3):
        r = d - (k * mid + c)
        sc = 1.4826 * float(np.median(np.abs(r))) + 1e-6
        k, c = np.polyfit(mid, d, 1, w=1 / (1 + (r / (2 * sc)) ** 2))
    return float(k), float(c)


def _edges(trow: np.ndarray, ref: float, p: float, y0: int, y1: int) -> Tuple[float, float]:
    t = _gsmooth(trow, p / 6)
    thr = EDGE_TEX_RATIO * ref
    run = max(2, int(p / 3))
    mid = (y0 + y1) // 2
    lo = mid
    while lo - run > 0 and not (t[lo - run:lo] < thr).all():
        lo -= 1
    hi = mid
    while hi + run < len(t) and not (t[hi:hi + run] < thr).all():
        hi += 1

    def cross(i: int, j: int) -> float:
        a, b = t[i], t[j]
        return float(i + (thr - a) / (b - a) * (j - i)) if b != a else float(i)

    return cross(max(0, lo - 1), lo), cross(hi, min(len(t) - 1, hi + 1))


def _groups(trow: np.ndarray, ref: float, p: float, e_lo: float, e_hi: float) -> List[Tuple[float, float]]:
    """다발 경계 [e_lo, e_hi] 안을 결 낮은 띠(틈, 0.35 피치 이상)로 가른 무리들. 틈이 없으면 [전체]."""
    t = _gsmooth(trow, p / 6)
    thr = EDGE_TEX_RATIO * ref
    lo, hi = max(0, int(np.ceil(e_lo))), min(len(t) - 1, int(np.floor(e_hi)))
    run_min = max(3, int(round(0.35 * p)))
    out: List[Tuple[float, float]] = []
    start = float(e_lo)
    i = lo
    while i <= hi:
        if t[i] < thr:
            j = i
            while j <= hi and t[j] < thr:
                j += 1
            if j - i >= run_min and i > lo and j <= hi:
                if i - start >= 0.5 * p:
                    out.append((start, float(i)))
                start = float(j)
            i = j
        else:
            i += 1
    if e_hi - start >= 0.5 * p:
        out.append((start, float(e_hi)))
    return out or [(float(e_lo), float(e_hi))]


def _lattice(Q, p, k, c, lo_y, hi_y, e_lo, e_hi) -> np.ndarray:
    """간격 p(y) 의 격자를 골(좁고 어두운 곳)에 맞춘다 — 위상 하나만 찾으므로 골 하나가 빠지거나 더해져도 안 흔들린다."""
    yy = np.arange(lo_y, hi_y + 1, dtype=float)
    pitch = np.clip(k * yy + c, 0.5 * p, 2 * p)
    phi = np.concatenate([[0.0], np.cumsum(1.0 / pitch[:-1])])
    V = (_gsmooth(Q, 0.5 * p) - _gsmooth(Q, p / 10))[lo_y:hi_y + 1]
    best = (-1e18, 0.0)
    for f0 in np.linspace(0, 1, 200, endpoint=False):
        kk = np.arange(np.ceil(phi[0] - f0), np.floor(phi[-1] - f0) + 1)
        yk = np.interp(kk + f0, phi, yy)
        m = (yk >= e_lo) & (yk <= e_hi)
        sc = float(np.interp(yk[m], yy, V).sum())
        if sc > best[0]:
            best = (sc, f0)
    f0 = best[1]
    kk = np.arange(np.ceil(phi[0] - f0), np.floor(phi[-1] - f0) + 1)
    return np.interp(kk + f0, phi, yy)


def _local_count(e_lo, e_hi, seams, pl) -> int:
    """교차 확인: 경계 안의 골로 나눈 조각을 국소 피치로 반올림해 더한다(짧은 조각은 이웃과 합친다)."""
    a, b = e_lo + 0.3 * pl(e_lo), e_hi - 0.3 * pl(e_hi)
    bounds = [e_lo] + [s for s in seams if a < s < b] + [e_hi]
    while len(bounds) > 2:
        w = [(bounds[i + 1] - bounds[i]) / pl(0.5 * (bounds[i] + bounds[i + 1])) for i in range(len(bounds) - 1)]
        i = int(np.argmin(w))
        if w[i] >= 0.65:
            break
        cand = []
        if i > 0:
            cand.append((w[i - 1], i))
        if i + 1 < len(w):
            cand.append((w[i + 1], i + 1))
        j = min(cand)[1]
        if not 0 < j < len(bounds) - 1:
            break
        bounds.pop(j)
    w = [(bounds[i + 1] - bounds[i]) / pl(0.5 * (bounds[i] + bounds[i + 1])) for i in range(len(bounds) - 1)]
    last = len(w) - 1
    return int(sum(max(1, int(round(x - 0.25))) if i in (0, last) else max(1, int(round(x)))
                   for i, x in enumerate(w)))


def _robust_line(x: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
    if len(x) < 3:
        return 0.0, float(np.median(y)) if len(y) else 0.0
    k, c = np.polyfit(x, y, 1)
    for _ in range(4):
        r = y - (k * x + c)
        sc = 1.4826 * float(np.median(np.abs(r))) + 1e-6
        k, c = np.polyfit(x, y, 1, w=1 / (1 + (r / (2 * sc)) ** 2))
    return float(k), float(c)


@dataclass(frozen=True)
class BundleTube:
    """다발 속 튜브 하나. 좌표는 정준 영상(튜브가 가로) 기준."""

    index: int            # 1..N (정준 영상 위에서 아래로)
    u0: float             # 편 영상에서 위·아래 경계(= xc 에서의 높이)
    u1: float
    x0: float             # 축 방향 끝(정준 x) — 파이프라인이 띠 영상에서 다시 잰다
    x1: float
    end_adjusted: bool = False   # 이웃과 동떨어져 이웃으로 맞춘 끝


@dataclass
class DenseBundle:
    tubes: List[BundleTube]
    pitch: float
    a: float
    b: float
    xc: float
    yc: float
    box: Tuple[int, int, int, int]
    count_check: int              # 교차 확인 개수(골 간격 반올림) — 참고값
    margin: float                 # 경계에서 가장 가까운 칸 중심까지(피치 비, 0~0.5) — 작을수록 애매
    notes: List[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.tubes)

    @property
    def uncertain(self) -> bool:
        """개수를 믿기 어렵다 — 바깥 칸이 경계에 걸쳤거나 두 셈이 2개 이상 다르다."""
        return self.margin < MARGIN_MIN or abs(self.count_check - self.count) >= 2

    def slope(self, u: float) -> float:
        return self.a + self.b * (u - self.yc)

    def y_at(self, u: float, x: float) -> float:
        """편 영상 높이 u 인 선이 정준 x 에서 지나는 y."""
        return u + self.slope(u) * (x - self.xc)

    def quad(self, t: BundleTube) -> np.ndarray:
        """튜브 네 모서리(정준 좌표) — 왼위·오위·오아래·왼아래."""
        return np.array([(t.x0, self.y_at(t.u0, t.x0)), (t.x1, self.y_at(t.u0, t.x1)),
                         (t.x1, self.y_at(t.u1, t.x1)), (t.x0, self.y_at(t.u1, t.x0))], dtype=np.float64)

    def ribbon(self, img: np.ndarray, t: BundleTube, *, margin: int, frac: float = 0.6):
        """튜브 중심선을 따라 가로로 편 띠 영상 + 제품 마스크.

        띠 높이 = 튜브 폭의 frac(이웃이 섞이지 않게 안쪽만). 길이 방향은 끝 바깥 margin px 씩.
        화소 간격은 1:1(회전만) — 띠에서 잰 px 길이가 원본 px 길이다.
        반환: (crop, mask, length_px, center, direction)
        """
        uc = 0.5 * (t.u0 + t.u1)
        p0 = (t.x0, self.y_at(uc, t.x0))
        p1 = (t.x1, self.y_at(uc, t.x1))
        return ribbon_crop(img, p0, p1, frac * (t.u1 - t.u0), margin=margin)


def ribbon_crop(img: np.ndarray, p0, p1, height: float, *, margin: int):
    """p0→p1 선분을 가로로 편 띠 영상(회전만, 1:1) + 제품 마스크(선분 길이만큼).

    반환: (crop, mask, length_px, center, direction) — 띠 x 는 center + (x − W/2)·direction.
    """
    p0, p1 = np.asarray(p0, dtype=np.float64), np.asarray(p1, dtype=np.float64)
    length = float(np.linalg.norm(p1 - p0))
    d = (p1 - p0) / max(length, 1e-9)
    nvec = np.array([-d[1], d[0]])
    hgt = max(5, int(round(height)))
    wid = int(round(length)) + 2 * margin
    cen = 0.5 * (p0 + p1)
    # 출력(ox, oy) → 입력 = cen + (ox − W/2)·d + (oy − H/2)·n
    M = np.array([[d[0], nvec[0], cen[0] - 0.5 * wid * d[0] - 0.5 * hgt * nvec[0]],
                  [d[1], nvec[1], cen[1] - 0.5 * wid * d[1] - 0.5 * hgt * nvec[1]]], dtype=np.float64)
    crop = cv2.warpAffine(img, M, (wid, hgt), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_REPLICATE)
    mask = np.zeros((hgt, wid), np.uint8)
    lo = int(round(0.5 * wid - 0.5 * length))
    mask[:, max(0, lo):min(wid, lo + int(round(length)))] = 255
    return crop, mask, length, cen, d


def find_dense_bundle(gray: np.ndarray, *, min_tubes: int = 3, valid: Optional[np.ndarray] = None,
                      pitch_hint: Optional[float] = None) -> Optional[DenseBundle]:
    """정준 영상(튜브가 대략 가로)에서 붙은 다발을 찾는다. 못 찾거나 min_tubes 미만이면 None.

    valid: 원래 사진이 있는 곳(255) — 돌려 세운 영상의 빈 모서리는 다발 위치 찾기에서 뺀다.
    pitch_hint: 튜브 한 개 폭(px, 기준정보 외경 ÷ 배율). 주면 간격을 그 0.8~1.3배 안에서만 찾는다 —
        틈이 섞인 다발에서 주기 추정이 엉뚱한 값(지름 32px 에 27px)으로 끌려가는 것을 막는다.
    """
    g = gray.astype(np.float32)
    box = _stripe_box(g, valid)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    w = x1 - x0
    pc = g[y0:y1, int(x0 + 0.45 * w):int(x0 + 0.55 * w) + 1].mean(axis=1)
    p_guess = _hinted_pitch(pc, pitch_hint) if pitch_hint else _global_pitch(pc)
    if not p_guess:
        return None
    a, b, xc, yc = _fan_fit(g, box, p_guess)
    R = _rectify(g, a, b, xc, yc)
    xa, xb = int(x0 + 0.2 * w), int(x0 + 0.8 * w) + 1
    Q = np.median(R[:, xa:xb], axis=1).astype(np.float64)
    p0 = _hinted_pitch(Q[y0:y1], pitch_hint) if pitch_hint else _global_pitch(Q[y0:y1])
    if not p0:
        return None
    T = _texture(R, p0)
    trow = np.median(T[:, xa:xb], axis=1)
    core = trow[int(y0 + 0.25 * (y1 - y0)):int(y1 - 0.25 * (y1 - y0)) + 1]
    ref = float(np.median(core)) if core.size else 0.0
    if ref <= 1e-3:
        return None
    e_lo, e_hi = _edges(trow, ref, p0, y0, y1)
    seams = _nms_seams(Q, p0, e_lo - p0, e_hi + p0)
    k, c = _pitch_line(seams, p0)

    def pl(y: float) -> float:
        return float(np.clip(k * y + c, 0.5 * p0, 2 * p0))

    if not (e_hi > e_lo and min(len(Q) - 1, int(e_hi + 3 * p0)) - max(0, int(e_lo - 3 * p0)) >= 2 * p0):
        return None
    # 틈(바닥이 보이는 결 낮은 띠)으로 무리를 나눠 무리마다 격자를 맞춘다(2026-10-24) — 붙은 무리 사이에
    # 틈이 있으면 격자 하나로는 틈마다 칸이 더 생기고(트레이 장면 +2~+10개) 위상도 어긋난다.
    cells: List[Tuple[float, float]] = []
    check = 0
    margin = 0.5
    for g_lo, g_hi in _groups(trow, ref, p0, e_lo, e_hi):
        lo_y, hi_y = max(0, int(g_lo - 3 * p0)), min(len(Q) - 1, int(g_hi + 3 * p0))
        lat = _lattice(Q, p0, k, c, lo_y, hi_y, g_lo, g_hi)
        a_in, b_in = g_lo + EDGE_SHRINK * pl(g_lo), g_hi - EDGE_SHRINK * pl(g_hi)
        cells.extend((float(u), float(v)) for u, v in zip(lat[:-1], lat[1:]) if a_in <= 0.5 * (u + v) <= b_in)
        check += _local_count(g_lo, g_hi, seams, pl)
        centers = 0.5 * (lat[:-1] + lat[1:])
        for edge in (a_in, b_in):
            dd = np.abs(centers - edge) / pl(edge)
            margin = min(margin, float(dd.min()) if dd.size else 0.5)
    if len(cells) < min_tubes:
        return None

    # 끝: 띠마다 축을 가로지르는 기울기(원통 음영)가 끊기는 곳
    G = np.abs(cv2.Sobel(R, cv2.CV_32F, 0, 1, ksize=3))

    def band(u: float, v: float) -> np.ndarray:
        return G[int(u + 0.15 * (v - u)):int(v - 0.15 * (v - u)) + 1]

    ca, cb = int(x0 + 0.3 * w), int(x1 - 0.3 * w) + 1
    gref = float(np.median([np.mean(band(u, v)[:, ca:cb]) for u, v in cells]))
    run = max(8, int(0.5 * p0))
    xm = int(0.5 * (x0 + x1))
    ends = []
    for u, v in cells:
        tx = np.mean(band(u, v), axis=0)
        tx = cv2.blur(tx.reshape(1, -1).astype(np.float32), (_odd(p0 / 2), 1)).ravel()
        csum = np.concatenate([[0], np.cumsum(tx < END_GRAD_RATIO * gref)])

        def lowrun(i: int, j: int) -> bool:
            i, j = max(0, i), min(len(tx), j)
            return j > i and csum[j] - csum[i] == j - i

        s = xm
        while s - run > 0 and not lowrun(s - run, s):
            s -= 1
        e = xm
        while e + run < len(tx) and not lowrun(e + 1, e + 1 + run):
            e += 1
        ends.append((float(s), float(e)))
    # 이웃과 동떨어진 끝(트레이 테두리가 띠와 나란히 붙어 새는 바깥 튜브 등)은 이웃으로 맞춘 값으로
    idx = np.arange(len(ends), dtype=float)
    ls, rs = np.array([e[0] for e in ends]), np.array([e[1] for e in ends])
    lmed = float(np.median(rs - ls))
    kl, cl = _robust_line(idx, ls)
    kr, cr = _robust_line(idx, rs)
    tubes: List[BundleTube] = []
    adjusted = 0
    for i, ((u, v), (s, e)) in enumerate(zip(cells, ends)):
        adj = False
        if abs(s - (kl * i + cl)) > 0.06 * lmed:
            s, adj = kl * i + cl, True
        if abs(e - (kr * i + cr)) > 0.06 * lmed:
            e, adj = kr * i + cr, True
        adjusted += int(adj)
        tubes.append(BundleTube(index=i + 1, u0=u, u1=v, x0=float(s), x1=float(e), end_adjusted=adj))
    notes = []
    if margin < MARGIN_MIN or abs(check - len(tubes)) >= 2:
        notes.append(f"다발 개수 불확실(경계 여유 {margin:.2f}, 골 간격 셈 {check}) — 재확인")
    if adjusted:
        notes.append(f"끝 위치를 이웃으로 맞춘 튜브 {adjusted}개")
    return DenseBundle(tubes=tubes, pitch=float(p0), a=a, b=b, xc=xc, yc=yc, box=box,
                       count_check=check, margin=margin, notes=notes)


__all__ = ["BundleTube", "DenseBundle", "MARGIN_MIN", "axis_angle", "find_dense_bundle", "ribbon_crop"]
