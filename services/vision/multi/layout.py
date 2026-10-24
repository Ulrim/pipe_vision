"""튜브 배치 찾기 — 덩어리 먼저, 붙은 덩어리만 나눈다 (어두운·밝은 배경 공통, 2026-10-24).

직접 만든 다발 장면 180장(vision.synth, 정답 자동 기록)으로 재 보니 종전 어두운 배경 분할의 개수
정확도가 8% 였다. 원인 셋:
- 튜브가 있는 행을 '화면 폭의 30% 넘게 덮인 행' 으로 찾았다 → 다발이 화면 폭의 30% 보다 좁으면
  하나도 못 찾았다. 60cm 설치에서 250mm 튜브가 화면 폭의 약 30% 다 — 현장에서 바로 터질 조건이었다.
- 튜브 사이 골을 '가로로 평균한 밝기' 로 찾았다 → 3° 만 기울어도 골이 번져 개수가 무너졌다.
- 떨어진 튜브와 붙은 튜브가 섞여도 전체를 한 덩어리로 보고 똑같은 간격으로 나눴다.

여기서는
1) 제품 마스크(어두운 배경 Otsu · 밝은 배경 contrast_mask, foreground.tube_mask)의 덩어리마다
   자기 축으로 길이·폭을 잰다(주성분 방향, 덩어리가 뭉툭하면 화면 대표 방향).
2) 기준자(눈금 띠)는 축을 따라 밝기가 주기적으로 바뀐다(눈금) — 튜브와 구별해 개수에서 뺀다.
3) 튜브 한 개 폭(기준정보 외경 ÷ 배율, 없으면 가늘고 긴 덩어리들의 폭)으로 덩어리마다 몇 개인지 본다.
   - 한 개: 그대로.
   - 2~3개: 폭을 똑같이 나눈 위치 근처의 골(어두운 틈)로 나눈다.
   - 그 이상: 그 덩어리만 줄무늬 분석(dense.py — 원근·기울기 대응)으로 나눈다. 두 셈이 어긋나면
     불확실(재확인)로 표시한다 — 조용히 고르지 않는다.
4) 튜브마다 중심선(양 끝)과 폭을 돌려준다 — 배치 검사는 중심선을 따라 편 띠(ribbon_crop)에서 잰다.

계산은 긴 변 1600px 이하로 줄인 영상에서 하고(파이 처리시간), 좌표는 원래 크기로 돌려준다.
결정적이며 학습이 필요 없다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

from ..preprocess.foreground import angle_gap, label_blobs, tube_mask
from .dense import axis_angle, find_dense_bundle

#: 배치 찾기 작업 해상도(긴 변 px).
WORK_SIDE = 1600
#: 폭 ÷ 튜브 한 개 폭이 이 값 미만이면 한 개.
SINGLE_MAX = 1.45
#: 이 개수 이하로 보이는 덩어리는 줄무늬 분석 대신 '똑같이 나눈 근처의 골' 로 나눈다(주기를 재기엔 짧다).
SPLIT_MAX = 3
#: 덩어리가 자기 축 사각형을 이만큼은 채워야 한다(튜브·다발은 속이 차 있다 — 트레이 테두리 고리는 0.1 아래).
#: 어둡게 찍혀 마스크가 듬성한 튜브도 0.4 안팎이라 여유를 둔다.
FILL_MIN = 0.25
#: 기준자 눈금 무늬의 최소 세기(축 방향 고주파 밝기의 표준편차, 그레이 단계).
RULER_MIN_STD = 10.0
#: 덩어리 주성분이 이만큼 길쭉해야 자기 방향을 쓴다(뭉툭하면 화면 대표 방향).
OWN_AXIS_ELONG = 4.0


@dataclass(frozen=True)
class TubeGeom:
    """튜브 하나 — 중심선 양 끝(p0 → p1)과 폭(px). 좌표는 find_layout 에 넣은 영상 기준."""

    p0: Tuple[float, float]
    p1: Tuple[float, float]
    width: float
    source: str = "blob"          # blob(한 덩어리=한 개) | split(폭으로 나눔) | dense(줄무늬 분석)
    end_adjusted: bool = False

    @property
    def length(self) -> float:
        return float(np.hypot(self.p1[0] - self.p0[0], self.p1[1] - self.p0[1]))

    @property
    def direction(self) -> np.ndarray:
        d = np.subtract(self.p1, self.p0).astype(np.float64)
        return d / max(float(np.linalg.norm(d)), 1e-9)

    @property
    def center(self) -> np.ndarray:
        return 0.5 * (np.asarray(self.p0, np.float64) + np.asarray(self.p1, np.float64))

    @property
    def slope(self) -> float:
        d = self.direction
        return float(d[1] / d[0]) if abs(d[0]) > 1e-9 else float("inf")

    def quad(self) -> np.ndarray:
        """네 모서리 — 왼위·오위·오아래·왼아래(y 가 아래로 커지는 영상 좌표)."""
        d = self.direction
        n = np.array([-d[1], d[0]]) * (0.5 * self.width)
        a, b = np.asarray(self.p0, np.float64), np.asarray(self.p1, np.float64)
        return np.array([a - n, b - n, b + n, a + n])


@dataclass
class Layout:
    tubes: List[TubeGeom]
    uncertain: bool = False
    notes: List[str] = field(default_factory=list)
    dense: bool = False           # 붙은 덩어리를 줄무늬로 나눈 곳이 있다
    rulers: int = 0               # 기준자로 보고 개수에서 뺀 띠
    unit_px: Optional[float] = None

    @property
    def count(self) -> int:
        return len(self.tubes)


@dataclass
class _BlobFrame:
    """덩어리 하나의 자기 축 좌표: a = 축 방향, b = 축을 가로지르는 방향(중심 c 기준)."""

    label: int
    box: Tuple[int, int, int, int]
    c: np.ndarray
    d: np.ndarray
    n: np.ndarray
    a: np.ndarray
    b: np.ndarray
    angle: float
    elong: float

    @property
    def a_lo(self) -> float:
        return float(self.a.min())

    @property
    def a_hi(self) -> float:
        return float(self.a.max())

    @property
    def b_lo(self) -> float:
        return float(self.b.min())

    @property
    def b_hi(self) -> float:
        return float(self.b.max())

    @property
    def length(self) -> float:
        return self.a_hi - self.a_lo + 1.0

    @property
    def width(self) -> float:
        return self.b_hi - self.b_lo + 1.0

    def point(self, a: float, b: float) -> Tuple[float, float]:
        p = self.c + a * self.d + b * self.n
        return float(p[0]), float(p[1])


def _pca(xs: np.ndarray, ys: np.ndarray) -> Tuple[np.ndarray, float, float]:
    c = np.array([xs.mean(), ys.mean()])
    cov = np.cov(np.vstack([xs - c[0], ys - c[1]]))
    w, v = np.linalg.eigh(cov)
    major = v[:, 1]
    ang = float(np.degrees(np.arctan2(major[1], major[0])))
    ang = ((ang + 90.0) % 180.0) - 90.0
    elong = float(np.sqrt(max(w[1], 1e-9) / max(w[0], 1e-9)))
    return c, ang, elong


def _frame(lab: np.ndarray, blob, angle: float) -> _BlobFrame:
    sub = lab[blob.y0:blob.y1, blob.x0:blob.x1] == blob.label
    ys, xs = np.nonzero(sub)
    xs = xs.astype(np.float64) + blob.x0
    ys = ys.astype(np.float64) + blob.y0
    c = np.array([xs.mean(), ys.mean()])
    t = np.radians(angle)
    d = np.array([np.cos(t), np.sin(t)])
    n = np.array([-d[1], d[0]])
    a = (xs - c[0]) * d[0] + (ys - c[1]) * d[1]
    b = (xs - c[0]) * n[0] + (ys - c[1]) * n[1]
    return _BlobFrame(label=blob.label, box=(blob.x0, blob.y0, blob.x1, blob.y1), c=c, d=d, n=n, a=a, b=b,
                      angle=angle, elong=0.0)


def _sample(g: np.ndarray, f: _BlobFrame, a0: float, a1: float, b0: float, b1: float,
            na: int, nb: int) -> np.ndarray:
    """덩어리 축 좌표의 격자(축 na × 가로 nb)로 영상을 다시 뽑는다 → (nb, na) float32."""
    aa = np.linspace(a0, a1, max(2, na), dtype=np.float32)[None, :]
    bb = np.linspace(b0, b1, max(2, nb), dtype=np.float32)[:, None]
    mx = (f.c[0] + aa * f.d[0] + bb * f.n[0]).astype(np.float32)
    my = (f.c[1] + aa * f.d[1] + bb * f.n[1]).astype(np.float32)
    return cv2.remap(g.astype(np.float32), mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _is_ruler(g: np.ndarray, f: _BlobFrame) -> bool:
    """축을 따라 밝기가 주기적으로 바뀌면(눈금) 기준자 — 튜브는 결이 축 방향이라 축을 따라 매끈하다."""
    na = int(f.length)
    if na < 64:
        return False
    hw = 0.25 * f.width
    bm = 0.5 * (f.b_lo + f.b_hi)
    prof = _sample(g, f, f.a_lo + 0.03 * na, f.a_hi - 0.03 * na, bm - hw, bm + hw, na, 5).mean(axis=0)
    k = np.exp(-0.5 * (np.arange(-18, 19) / 6.0) ** 2)
    k /= k.sum()
    hp = prof - np.convolve(np.pad(prof, 18, mode="edge"), k, mode="valid")
    hp = hp - hp.mean()
    # 눈금은 진하다(흰 바탕 검은 선) — JPEG 8px 블록 무늬처럼 옅은 주기는 기준자가 아니다.
    if float(hp.std()) < RULER_MIN_STD:
        return False
    e = float(hp @ hp)
    n = len(hp)
    ac = np.correlate(hp, hp, mode="full")[n - 1:] / e
    lo, hi = 4, min(90, n // 8)
    if hi <= lo:
        return False
    # 첫 번째 0 교차 뒤에서 가장 높은 자기상관 — 눈금 간격에서 높게 되돌아온다
    neg = np.flatnonzero(ac[1:hi + 1] < 0)
    if neg.size == 0:
        return False
    start = max(lo, int(neg[0]) + 1)
    if start >= hi:
        return False
    return float(ac[start:hi + 1].max()) > 0.45


def _cross_profile(g: np.ndarray, f: _BlobFrame) -> Tuple[np.ndarray, float]:
    """축 가운데 60% 를 평균한 가로 방향 밝기(1px 간격) — (프로파일, 시작 b)."""
    nb = int(np.ceil(f.width)) + 1
    span = f.a_hi - f.a_lo
    smp = _sample(g, f, f.a_lo + 0.2 * span, f.a_hi - 0.2 * span, f.b_lo, f.b_lo + nb - 1,
                  max(8, int(0.6 * span / 2)), nb)
    return np.median(smp, axis=1), f.b_lo


def _snap_seams(prof: np.ndarray, n: int) -> List[float]:
    """n 등분 위치 근처(±0.3 칸)에서 가장 어두운 곳으로 경계를 옮긴다."""
    w = len(prof)
    step = w / float(n)
    k = max(1, int(round(step / 10)))
    sp = np.convolve(np.pad(prof, k, mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
    out: List[float] = []
    prev = 0.0
    for j in range(1, n):
        c = j * step
        lo, hi = int(max(prev + 1, c - 0.3 * step)), int(min(w - 2, c + 0.3 * step))
        if hi <= lo:
            out.append(c)
        else:
            out.append(float(lo + int(np.argmin(sp[lo:hi + 1]))))
        prev = out[-1]
    return out


def _valley_seams(prof: np.ndarray, unit: Optional[float]) -> Tuple[List[float], bool]:
    """튜브 사이 골(어두운 틈) — 돌출도가 덩어리 밝기 폭의 25% 넘는 극소, 서로 0.55 피치 넘게.

    반환: (골 위치 목록, 간격이 고른가). 덩어리 양 끝 0.35 피치 안의 골은 버린다(바깥 가장자리 음영).
    """
    n = len(prof)
    if n < 8:
        return [], False
    k = max(1, int(round((unit or n / 6.0) / 12.0)))
    sp = np.convolve(np.pad(prof.astype(np.float64), k, mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
    rng = float(sp.max() - sp.min())
    if rng < 8.0:
        return [], False
    mins = [i for i in range(1, n - 1) if sp[i] <= sp[i - 1] and sp[i] < sp[i + 1]]
    prom = {i: _prominence(sp, i) for i in mins}
    cand = [i for i in mins if prom[i] >= 0.25 * rng]
    if not cand:
        return [], False
    pitch = float(unit) if unit else (float(np.median(np.diff(sorted(cand)))) if len(cand) >= 2 else n / 2.0)
    keep: List[int] = []
    for i in sorted(cand, key=lambda c: -prom[c]):
        if all(abs(i - q) >= 0.55 * pitch for q in keep) and 0.35 * pitch <= i <= n - 1 - 0.35 * pitch:
            keep.append(i)
    keep.sort()
    if not keep:
        return [], False
    if unit is None and len(keep) == 1 and prom[keep[0]] < 0.45 * rng:
        # 폭 기준 없이 골 하나로 둘로 나누려면 깊어야 한다 — 튜브 한 개 안의 반사 띠 사이도 옅은 골을 만든다
        return [], False
    d = np.diff([0] + keep + [n - 1]).astype(float)
    inner = d[1:-1] if len(d) > 2 else d
    regular = bool(len(inner) >= 1 and float(np.std(inner)) <= 0.25 * float(np.mean(inner)))
    return [float(x) for x in keep], regular


def _prominence(p: np.ndarray, idx: int) -> float:
    v = float(p[idx])
    i, lm = idx - 1, v
    while i >= 0 and p[i] >= v:
        lm = max(lm, float(p[i]))
        i -= 1
    j, rm = idx + 1, v
    while j < len(p) and p[j] >= v:
        rm = max(rm, float(p[j]))
        j += 1
    return min(lm, rm) - v


def _cut(f: _BlobFrame, cuts: List[float]) -> List[TubeGeom]:
    """덩어리를 축을 가로지르는 경계(b 좌표)로 자른 튜브들."""
    bounds = [f.b_lo] + sorted(cuts) + [f.b_hi + 1.0]
    out: List[TubeGeom] = []
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        sel = (f.b >= lo) & (f.b < hi)
        if not sel.any() or hi - lo < 2:
            continue
        a = f.a[sel]
        bm = 0.5 * (lo + hi)
        out.append(TubeGeom(f.point(float(a.min()), bm), f.point(float(a.max()), bm), hi - lo, source="split"))
    return out


def _split(g: np.ndarray, f: _BlobFrame, n: int) -> List[TubeGeom]:
    """덩어리를 축을 가로질러 n 개로 — 경계는 똑같이 나눈 위치 근처의 골."""
    prof, b0 = _cross_profile(g, f)
    return _cut(f, [b0 + x for x in _snap_seams(prof, n)])


def _unit_from_widths(ws: List[float]) -> Optional[float]:
    """덩어리 폭들을 '한 폭 × 정수' 로 가장 잘 설명하는 한 폭.

    후보 = 각 폭 ÷ 1·2·3. 점수 = 상대 잔차(나쁜 20% 는 버린 평균 — 어둡게 찍혀 깎인 덩어리 하나에 끌려가지
    않게) + 0.06 × (덩어리당 평균 개수 − 1)(마스크 폭은 ±20% 흔들리므로 잘게 쪼갠 폭이 잔차로 이기지 않게,
    같게 설명되면 한 개짜리로 본다). 두 개짜리가 더 많아도 한 개짜리 몇 개가 있으면 그 폭이 이긴다.
    """
    if not ws:
        return None
    w = np.asarray(ws, dtype=np.float64)
    best: Optional[Tuple[float, float]] = None
    floor = max(4.0, float(np.median(w)) / 3.2)     # 가는 덩어리 중앙값이 튜브 3개를 넘는 일은 없다
    for u in sorted({float(x) / k for x in w for k in (1, 2, 3)}):
        if u < floor:
            continue
        r = w / u
        k = np.maximum(1.0, np.round(r))
        if k.max() > 6:
            continue
        res = np.sort(np.abs(r - k) / k)
        keep = res[:max(1, int(np.ceil(0.8 * len(res))))]
        score = float(keep.mean()) + 0.06 * (float(k.mean()) - 1.0)
        if best is None or score < best[0]:
            best = (score, u)
    return best[1] if best else None


def _single(f: _BlobFrame) -> TubeGeom:
    bm = 0.5 * (f.b_lo + f.b_hi)
    return TubeGeom(f.point(f.a_lo, bm), f.point(f.a_hi, bm), f.width, source="blob")


def _dense_in_blob(g: np.ndarray, lab: np.ndarray, f: _BlobFrame, pad: int):
    """덩어리 하나만 남긴 영상(밖은 배경 밝기)에서 줄무늬 분석 — (TubeGeom 목록, DenseBundle) 또는 None."""
    h, w = g.shape
    x0, y0, x1, y1 = f.box
    x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)
    sub = g[y0:y1, x0:x1].astype(np.float32)
    own = (lab[y0:y1, x0:x1] == f.label).astype(np.uint8)
    keep = cv2.dilate(own, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    bgv = float(np.median(sub[keep == 0])) if (keep == 0).any() else float(np.median(sub))
    clean = np.where(keep > 0, sub, bgv).astype(np.float32)
    bundle = find_dense_bundle(clean, min_tubes=2, valid=keep * 255)
    if bundle is None:
        return None
    out = []
    for t in bundle.tubes:
        uc = 0.5 * (t.u0 + t.u1)
        p0 = (t.x0 + x0, bundle.y_at(uc, t.x0) + y0)
        p1 = (t.x1 + x0, bundle.y_at(uc, t.x1) + y0)
        out.append(TubeGeom(p0, p1, t.u1 - t.u0, source="dense", end_adjusted=t.end_adjusted))
    return out, bundle


def _scale(t: TubeGeom, k: float) -> TubeGeom:
    if k == 1.0:
        return t
    return TubeGeom((t.p0[0] * k, t.p0[1] * k), (t.p1[0] * k, t.p1[1] * k), t.width * k,
                    source=t.source, end_adjusted=t.end_adjusted)


def find_layout(gray: np.ndarray, *, light: bool, valid: Optional[np.ndarray] = None,
                unit_px: Optional[float] = None) -> Layout:
    """정준 영상(튜브가 대략 가로, ±8° 안)에서 튜브 배치를 찾는다.

    light: 배경이 밝은가(전체 프레임에서 정한 값). valid: 원래 사진이 있는 곳(255).
    unit_px: 튜브 한 개 폭(px, 이 영상 기준) — 기준정보 외경 ÷ 배율. 없으면 덩어리들에서 추정.
    """
    h, w = gray.shape[:2]
    s = min(1.0, WORK_SIDE / float(max(h, w)))
    g = gray if s == 1.0 else cv2.resize(gray, (max(1, int(w * s)), max(1, int(h * s))),
                                         interpolation=cv2.INTER_AREA)
    mask = tube_mask(g, light=light).copy()
    if valid is not None:
        vm = valid if s == 1.0 else cv2.resize(valid, (g.shape[1], g.shape[0]), interpolation=cv2.INTER_NEAREST)
        mask[vm == 0] = 0
    lab, blobs = label_blobs(mask, min_area_frac=0.0003)
    if not blobs:
        return Layout(tubes=[])

    # 덩어리마다 주성분 방향 → 화면 대표 방향(길쭉한 덩어리들의 면적 가중 평균, 없으면 모서리 방향)
    pcs = []
    for b in blobs:
        sub = lab[b.y0:b.y1, b.x0:b.x1] == b.label
        ys, xs = np.nonzero(sub)
        pcs.append(_pca(xs.astype(np.float64) + b.x0, ys.astype(np.float64) + b.y0))
    el = [(b.area, ang) for b, (_c, ang, e) in zip(blobs, pcs) if e >= OWN_AXIS_ELONG and abs(ang) < 20.0]
    if el:
        wts = np.array([a for a, _ in el], float)
        th = np.radians([2.0 * ang for _, ang in el])
        phi_g = 0.5 * float(np.degrees(np.arctan2((wts * np.sin(th)).sum(), (wts * np.cos(th)).sum())))
    else:
        ga = axis_angle(g)
        phi_g = float(ga) if ga is not None and abs(ga) < 20.0 else 0.0

    frames: List[_BlobFrame] = []
    for b, (_c, ang, e) in zip(blobs, pcs):
        own = e >= OWN_AXIS_ELONG and angle_gap(ang, phi_g) < 10.0
        f = _frame(lab, b, ang if own else phi_g)
        f.elong = e
        frames.append(f)

    rulers = [f for f in frames if _is_ruler(g, f)]
    cand = [f for f in frames if f not in rulers]
    if not cand:
        return Layout(tubes=[], rulers=len(rulers))
    # 속이 찬 덩어리만 — 트레이 테두리처럼 고리 모양(자기 축 사각형을 거의 못 채움)은 튜브가 아니다
    cand = [f for f in cand if len(f.a) >= FILL_MIN * f.length * f.width]
    if not cand:
        return Layout(tubes=[], rulers=len(rulers))
    l_max = max(f.length for f in cand)
    cand = [f for f in cand if f.length >= max(20.0, 0.3 * l_max) and f.width >= 3.0]

    # 튜브 한 개 폭 — 기준정보 외경이 있으면 그것, 없으면 가늘고 긴 덩어리들의 폭이 '한 폭의 정수배' 로
    # 가장 잘 설명되는 값. 그 다음 큰 다발을 줄무늬로 나눠 얻은 간격(피치)이 더 믿을 만하면 그것으로 바꾼다.
    unit = unit_px * s if unit_px and unit_px > 0 else None
    given = unit is not None
    if unit is None:
        # 덩어리 하나로는 폭 기준을 못 세운다(그 폭이 곧 기준이 돼 무엇이든 한 개가 된다)
        thin = [f.width for f in cand if f.length / f.width >= 5.0]
        unit = _unit_from_widths(thin) if len(thin) >= 2 else None

    tubes: List[TubeGeom] = []
    notes: List[str] = []
    uncertain = False
    dense_used = False
    pad = int(round(unit)) if unit else 10

    # 큰 덩어리(줄무늬 분석 대상)부터 — 믿을 만한 다발의 간격으로 한 폭을 바로잡는다
    big = [f for f in cand if unit is None or f.width / unit >= SPLIT_MAX + 0.5]
    dense_res = {id(f): _dense_in_blob(g, lab, f, pad) for f in big}
    by_id = {id(f): f for f in big}
    # 칸 폭의 중앙값(실제로 나눈 폭) — 주기 추정값(pitch)은 반사 띠의 배음에 끌려 틀릴 때가 있다
    pitches = [float(np.median([t.width for t in got[0]])) for key, got in dense_res.items() if got is not None
               and not got[1].uncertain and got[1].count >= 5
               and 0.8 <= sum(t.width for t in got[0]) / by_id[key].width <= 1.2]
    if not given and pitches:
        pu = float(np.median(pitches))
        if unit is None or not (0.65 <= unit / pu <= 1.5):
            notes.append(f"튜브 한 개 폭을 다발 간격으로 바로잡음({0 if unit is None else unit:.0f} → {pu:.0f}px)")
            unit = pu
            pad = int(round(unit))

    for f in cand:
        r = f.width / unit if unit else None
        if r is not None and r < SINGLE_MAX:
            tubes.append(_single(f))
            continue
        prof, b0 = _cross_profile(g, f)
        seams, regular = _valley_seams(prof, unit)
        opts = []                                   # (이름, 튜브들, 줄무늬 DenseBundle 또는 None)
        if seams:
            opts.append(("valley", _cut(f, [b0 + x for x in seams]), None))
        if r is None or r >= SPLIT_MAX + 0.5:
            got = dense_res[id(f)] if id(f) in dense_res else _dense_in_blob(g, lab, f, pad)
            # 줄무늬 칸들이 덩어리 폭을 대부분 덮어야 한다(엉뚱한 주기를 잡으면 몇 칸만 남는다)
            if got is not None and 0.75 <= sum(t.width for t in got[0]) / f.width <= 1.25:
                opts.append(("dense", got[0], got[1]))
        if r is not None:
            n = max(1, int(round(r)))
            if n >= 2:
                opts.append(("width", _split(g, f, n), None))
            # 폭 기준 허용 범위 안의 셈 중 줄무늬(원근 대응) > 골 > 똑같이 나눔. 범위 안에 없으면 가장 가까운 것.
            rank = {"dense": 0, "valley": 1, "width": 2}
            tol = max(0.6, 0.12 * r)
            inside = [o for o in opts if abs(len(o[1]) - r) <= tol]
            if inside:
                name, geoms, bundle = min(inside, key=lambda o: rank[o[0]])
            elif opts:
                name, geoms, bundle = min(opts, key=lambda o: (abs(len(o[1]) - r), rank[o[0]]))
            else:
                name, geoms, bundle = "blob", [_single(f)], None
            if abs(len(geoms) - r) > max(0.45, 0.1 * r) or (bundle is not None and bundle.uncertain):
                uncertain = True
                notes.append(f"덩어리 폭 튜브 {r:.1f}개분 — {name} 셈 {len(geoms)}개(재확인)")
        else:
            d = next((o for o in opts if o[0] == "dense"), None)
            v = next((o for o in opts if o[0] == "valley"), None)
            if d is not None and (v is None or abs(len(v[1]) - len(d[1])) <= 1 or not d[2].uncertain):
                name, geoms, bundle = d
                if bundle.uncertain or (v is not None and abs(len(v[1]) - len(d[1])) >= 2):
                    uncertain = True
                    notes.append(f"줄무늬 셈 {len(d[1])}개, 골 셈 {len(v[1]) if v else '-'}개(재확인)")
            elif v is not None and regular:
                name, geoms, bundle = v
            else:
                name, geoms, bundle = "blob", [_single(f)], None
                if f.length / max(f.width, 1.0) < 5.0:
                    uncertain = True
                    notes.append("튜브 한 개 폭을 알 수 없는 뭉툭한 덩어리 — 1개로 셈(재확인, 기준정보 외경 입력 권장)")
        if bundle is not None:
            dense_used = True
            notes.extend(bundle.notes)
        tubes.extend(geoms)

    # 축을 가로지르는 순서(위 → 아래)
    t = np.radians(phi_g)
    n_g = np.array([-np.sin(t), np.cos(t)])
    tubes.sort(key=lambda q: float(q.center @ n_g))
    k = 1.0 / s
    return Layout(tubes=[_scale(q, k) for q in tubes], uncertain=uncertain, notes=notes, dense=dense_used,
                  rulers=len(rulers), unit_px=(unit * k) if unit else None)


def layout_from_bundle(bundle) -> Layout:
    """화면 전체 줄무늬 분석(DenseBundle) → Layout (밝은 배경에서 덩어리가 안 잡힐 때)."""
    out = []
    for t in bundle.tubes:
        uc = 0.5 * (t.u0 + t.u1)
        out.append(TubeGeom((t.x0, bundle.y_at(uc, t.x0)), (t.x1, bundle.y_at(uc, t.x1)), t.u1 - t.u0,
                            source="dense", end_adjusted=t.end_adjusted))
    return Layout(tubes=out, uncertain=bundle.uncertain, notes=list(bundle.notes), dense=True)


__all__ = ["Layout", "TubeGeom", "find_layout", "layout_from_bundle"]
