"""튜브 중심선 확인 — 학습 모델(선택, 2026-11-15). 밝은 바닥(트레이) 사진의 개수를 **바로 고친다**.

줄무늬 격자로 센 다발(layout.py·dense.py)에 남는 오류는 대부분 다발 **바깥**에 있다: 트레이 벽·그늘을 맨 끝
튜브로 하나 더 세거나(+1), 다발 옆에 떨어져 놓인 낱개·계단처럼 짧게 놓인 튜브·그늘 속 튜브를 빠뜨린다(−1~−4).
작은 분할망(ResNet18 인코더 + U-Net 디코더)이 사진에서 **튜브 몸통**과 **튜브 중심선**을 화소마다 예측하고,
셈의 칸과 대조한다.

- **빼기:** 맨 바깥 칸(양쪽 한 칸씩)을 따라 중심선이 없으면(지지값 < S_MIN) 그 칸을 뺀다. 안쪽 칸은 보지 않는다.
- **더하기:** 셈의 칸 밖에 있는 중심선 조각 중 길이가 칸 길이의 F_MIN 이상이고, 그 자리 몸통 확률이 B_MIN 이상인
  것을 튜브로 더한다(나란하고 0.6폭 안에 붙은 겹선은 하나로, 최대 ADD_MAX 개).
- 중심선은 헤시안으로 가로 방향 극대만 남기고(1px), **넓은 창의 튜브 방향과 맞는 화소끼리만** 잇는다 — 끝단에서
  중심선들이 빗처럼 이어지는 것을 막는다.

휴대폰 트레이 사진 425장(생성 이미지로 보이는 사진 제외) 50장 블록 2겹 교차검증 — 모델은 다른 겹의 '셈이 맞은
사진' 으로만 학습, 문턱도 다른 겹에서 골랐다: 재확인 없이 382 → **408(96.0%)**, 32장 고침·6장 망가뜨림.
docs/TRAINING_DATA.md §3-6.

모델 파일은 저장소에 없다(고객 사진으로 학습 — 저장소가 공개다). `AIVIS_RIDGE_MODEL=<ridge.onnx>` 를 주면 켜지고,
없거나 onnxruntime 이 없거나 로드에 실패하면 **조용히 꺼진다**(종전 동작 그대로). 학습한 영역이 밝은 바닥 트레이
사진(긴 변 640px 로 줄여 학습)이라 **밝은 배경에서만** 쓴다 — 현장 권장(무광 검정 바닥)에서는 보지 않는다.
"""
from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

#: 모델 입력: 원본 사진 긴 변을 이 크기로(학습과 같게)
MAX_SIDE = 640
#: 출력 해상도 = 입력 / STRIDE
STRIDE = 2
#: 이보다 튜브가 적으면 보지 않는다
MIN_TUBES = 4
#: 칸 밖 중심선 문턱
T_LINE = 0.4
#: 맨 바깥 칸 중심선 지지값 하한 — 아래면 뺀다
S_MIN = 0.3
#: 양쪽에서 뺄 수 있는 칸 수(맨 바깥부터 연속)
RM_PER_SIDE = 1
#: 더할 조각의 길이 하한(셈 칸 길이 중앙값 대비)
F_MIN = 0.3
#: 조각 표본 중 칸 밖 비율 하한
O_MIN = 0.7
#: 더할 조각 자리의 몸통 확률(중앙값) 하한
B_MIN = 0.9
#: 한 사진에서 더할 수 있는 최대 개수
ADD_MAX = 4

Seg = Tuple[np.ndarray, np.ndarray, float]


def quad_seg(q: np.ndarray) -> Seg:
    """사각형 → (끝점 a, 끝점 b, 폭). 짧은 두 변의 중점이 끝점(꼭짓점 순서와 무관)."""
    q = np.asarray(q, np.float64).reshape(4, 2)
    e = [float(np.linalg.norm(q[i] - q[(i + 1) % 4])) for i in range(4)]
    if e[0] + e[2] < e[1] + e[3]:
        return 0.5 * (q[0] + q[1]), 0.5 * (q[2] + q[3]), 0.5 * (e[0] + e[2])
    return 0.5 * (q[1] + q[2]), 0.5 * (q[3] + q[0]), 0.5 * (e[1] + e[3])


def thin_lines(ridge: np.ndarray, t: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """중심선 지도 → (1px 중심선 마스크, 넓은 창 선 방향 x, y)."""
    r = cv2.GaussianBlur(ridge.astype(np.float32), (0, 0), 1.0)
    rxx = cv2.Sobel(r, cv2.CV_32F, 2, 0, ksize=3)
    ryy = cv2.Sobel(r, cv2.CV_32F, 0, 2, ksize=3)
    rxy = cv2.Sobel(r, cv2.CV_32F, 1, 1, ksize=3)
    tr = rxx + ryy
    dt = np.sqrt(((rxx - ryy) * 0.5) ** 2 + rxy ** 2)
    lam = 0.5 * tr - dt                                   # 가로지르는 방향 곡률(가장 음수)
    lam2 = 0.5 * tr + dt
    # 그 고유벡터 — 두 표현 중 큰 쪽(축에 나란한 선에서 한쪽이 0 이 된다)
    ax, ay = rxy, lam - rxx
    bx, by = lam - ryy, rxy
    use_a = (ax ** 2 + ay ** 2) >= (bx ** 2 + by ** 2)
    vx = np.where(use_a, ax, bx)
    vy = np.where(use_a, ay, by)
    bad = (np.abs(vx) + np.abs(vy)) < 1e-9
    vx = np.where(bad, 1.0, vx)
    vy = np.where(bad, 0.0, vy)
    nn = np.sqrt(vx ** 2 + vy ** 2)
    vx = (vx / nn).astype(np.float32)
    vy = (vy / nn).astype(np.float32)
    h, w = r.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    a = cv2.remap(r, xx + vx, yy + vy, cv2.INTER_LINEAR)
    b = cv2.remap(r, xx - vx, yy - vy, cv2.INTER_LINEAR)
    # 넓은 창(구조 텐서) 방향 — 끝단에서도 튜브 방향을 가리킨다
    gx = cv2.Sobel(r, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(r, cv2.CV_32F, 0, 1, ksize=3)
    jxx = cv2.GaussianBlur(gx * gx, (0, 0), 6.0)
    jyy = cv2.GaussianBlur(gy * gy, (0, 0), 6.0)
    jxy = cv2.GaussianBlur(gx * gy, (0, 0), 6.0)
    th = 0.5 * np.arctan2(2 * jxy, jxx - jyy)
    lx_w, ly_w = -np.sin(th), np.cos(th)
    agree = np.abs(-vy * lx_w + vx * ly_w) > 0.85
    keep = (r >= a) & (r >= b) & (r > t) & (lam < 0) & (lam2 > 0.5 * lam) & agree
    return keep.astype(np.uint8), lx_w.astype(np.float32), ly_w.astype(np.float32)


def line_pieces(ridge: np.ndarray, t: float, min_px: int = 4) -> List[Dict[str, Any]]:
    """1px 중심선을 선 방향으로만 이어 조각으로 — 각 조각의 중심·방향·길이."""
    m, dx, dy = thin_lines(ridge, t)
    h, w = m.shape
    ys, xs = np.nonzero(m)
    n = len(ys)
    if n == 0:
        return []
    idx = -np.ones((h, w), np.int64)
    idx[ys, xs] = np.arange(n)
    rows, cols = [], []
    for oy, ox in ((0, 1), (1, 0), (1, 1), (1, -1), (0, 2), (2, 0), (2, 1), (1, 2), (2, -1), (1, -2)):
        nrm = math.hypot(ox, oy)
        y2, x2 = ys + oy, xs + ox
        ok = (y2 >= 0) & (y2 < h) & (x2 >= 0) & (x2 < w)
        y1, x1, y2, x2 = ys[ok], xs[ok], y2[ok], x2[ok]
        j = idx[y2, x2]
        good = j >= 0
        good &= np.abs(dx[y1, x1] * ox + dy[y1, x1] * oy) / nrm > 0.6
        good &= np.abs(dx[y2, x2] * ox + dy[y2, x2] * oy) / nrm > 0.6
        rows.append(idx[y1, x1][good])
        cols.append(j[good])
    r = np.concatenate(rows)
    c = np.concatenate(cols)
    lab = np.arange(n)
    for _ in range(1000):                                # 이웃 최솟값 전파 + 포인터 점프
        old = lab.copy()
        np.minimum.at(lab, r, lab[c])
        np.minimum.at(lab, c, lab[r])
        lab = lab[lab]
        if np.array_equal(old, lab):
            break
    _, lab = np.unique(lab, return_inverse=True)
    out = []
    for k in range(int(lab.max()) + 1):
        sel = lab == k
        if int(sel.sum()) < min_px:
            continue
        p = np.stack([xs[sel], ys[sel]], 1).astype(np.float64)
        cen = p.mean(0)
        cov = np.cov((p - cen).T) if len(p) > 2 else np.eye(2)
        _ev, evec = np.linalg.eigh(cov)
        u = evec[:, 1]
        proj = (p - cen) @ u
        out.append({"c": cen, "u": u, "lo": float(proj.min()), "hi": float(proj.max()),
                    "L": float(proj.max() - proj.min() + 1)})
    return out


def _support(ridge: np.ndarray, seg: Seg, n: int = 24) -> float:
    """칸 중심선을 따라(15~85%) '가로 ±0.3폭 안 최대 중심선 값' 의 중앙값."""
    a, b, w = seg
    d = b - a
    if float(np.linalg.norm(d)) < 2:
        return 0.0
    u = d / float(np.linalg.norm(d))
    nv = np.array([-u[1], u[0]])
    h, wd = ridge.shape
    vals = []
    for t in np.linspace(0.15, 0.85, n):
        p = a + d * t
        best = 0.0
        for k in np.linspace(-0.3 * w, 0.3 * w, 5):
            x, y = p + nv * k
            xi, yi = int(round(x)), int(round(y))
            if 0 <= xi < wd and 0 <= yi < h:
                best = max(best, float(ridge[yi, xi]))
        vals.append(best)
    return float(np.median(vals))


def _order(segs: List[Seg]) -> List[int]:
    """칸을 다발 가로 방향 순서로(맨 바깥이 양 끝)."""
    if len(segs) < 2:
        return list(range(len(segs)))
    dirs = []
    for a, b, _w in segs:
        d = (b - a) / max(float(np.linalg.norm(b - a)), 1e-6)
        dirs.append(d if d[0] + d[1] >= 0 else -d)
    u = np.mean(dirs, 0)
    nv = np.array([-u[1], u[0]])
    key = [float((0.5 * (a + b)) @ nv) for a, b, _w in segs]
    return [int(i) for i in np.argsort(key)]


@dataclass
class RidgeCheck:
    """셈과 중심선의 대조 결과. 좌표는 check() 에 준 영상 좌표."""

    removed: List[int] = field(default_factory=list)      # 뺀 칸(입력 순서 번호)
    added: List[Seg] = field(default_factory=list)         # 더한 튜브(a, b, 폭)
    edge_support: Tuple[float, float] = (1.0, 1.0)         # 양쪽 맨 바깥 칸 지지값

    @property
    def changed(self) -> bool:
        return bool(self.removed or self.added)

    def note(self) -> str:
        parts = []
        if self.removed:
            parts.append(f"맨 바깥 칸 {len(self.removed)}개에 튜브 중심선이 없어 뺐다(트레이 벽·그늘)")
        if self.added:
            parts.append(f"다발 밖 튜브 {len(self.added)}개를 더했다(떨어진 낱개·짧은 튜브)")
        return "튜브 중심선 확인: " + ", ".join(parts)

    def info(self) -> Dict[str, Any]:
        return {"removed": len(self.removed), "added": len(self.added),
                "edge_support": [round(float(x), 3) for x in self.edge_support]}


def decide(ridge: np.ndarray, body: np.ndarray, segs: List[Seg]) -> RidgeCheck:
    """지도(출력 해상도)와 칸(같은 좌표) → 뺄 칸·더할 튜브."""
    order = _order(segs)
    sup = {i: _support(ridge, segs[i]) for i in order}
    removed: List[int] = []
    for seq in (order, order[::-1]):
        for i in seq[:RM_PER_SIDE]:
            if sup[i] < S_MIN and i not in removed:
                removed.append(i)
            else:
                break
    h, w = ridge.shape
    inside = np.zeros((h, w), np.uint8)
    for a, b, wd in segs:
        d = b - a
        ln = float(np.linalg.norm(d))
        u = d / max(ln, 1e-6)
        nv = np.array([-u[1], u[0]])
        e = 0.6 * wd
        pa, pb = a - u * 0.05 * ln, b + u * 0.05 * ln
        cv2.fillPoly(inside, [np.round(np.array([pa + nv * e, pb + nv * e, pb - nv * e, pa - nv * e])).astype(np.int32)], 1)
    lq = float(np.median([np.linalg.norm(b - a) for a, b, _ in segs]))
    wq = float(np.median([wd for _, _, wd in segs]))
    cands = []
    for pc in line_pieces(ridge, T_LINE):
        pts = [pc["c"] + pc["u"] * e for e in np.linspace(pc["lo"], pc["hi"], 9)]
        out = sum(1 for p in pts if not (0 <= int(round(p[0])) < w and 0 <= int(round(p[1])) < h
                                         and inside[int(round(p[1])), int(round(p[0]))]))
        if out / 9.0 < O_MIN or pc["L"] / max(lq, 1e-6) < F_MIN:
            continue
        mb = [float(body[int(round(p[1])), int(round(p[0]))])
              for p in (pc["c"] + pc["u"] * e for e in np.linspace(pc["lo"], pc["hi"], 15))
              if 0 <= int(round(p[0])) < w and 0 <= int(round(p[1])) < h]
        if not mb or float(np.median(mb)) < B_MIN:
            continue
        cands.append(pc)
    cands.sort(key=lambda o: -o["L"])
    kept: List[Dict[str, Any]] = []
    for o in cands:
        dup = False
        for k in kept:
            if abs(float(o["u"] @ k["u"])) < math.cos(math.radians(15)):
                continue
            nv = np.array([-k["u"][1], k["u"][0]])
            if abs(float((o["c"] - k["c"]) @ nv)) < 0.6 * wq:
                dup = True
                break
        if not dup:
            kept.append(o)
    added = [(o["c"] + o["u"] * o["lo"], o["c"] + o["u"] * o["hi"], wq) for o in kept[:ADD_MAX]]
    edge = (sup[order[0]], sup[order[-1]]) if order else (1.0, 1.0)
    return RidgeCheck(removed=removed, added=added, edge_support=edge)


class RidgeChecker:
    """ONNX(입력 (1,3,H,W) RGB 0..1 → 출력 (1,2,H/2,W/2) 로짓[몸통, 중심선]) — 원본·좌우·상하 뒤집기 평균(검증과 같다)."""

    def __init__(self, path: str):
        import onnxruntime as ort  # type: ignore

        self.path = path
        self.sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name
        self.max_side = MAX_SIDE
        side = Path(path).with_suffix(".json")
        if side.exists():
            meta = json.loads(side.read_text(encoding="utf-8"))
            if meta.get("kind") not in (None, "tube_ridge"):
                raise ValueError(f"사이드카 kind={meta.get('kind')!r} — tube_ridge 모델이 아니다")
            self.max_side = int(meta.get("max_side", MAX_SIDE))

    def maps(self, bgr: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """BGR(이미 모델 배율) → (몸통, 중심선) 확률, 입력/2 해상도."""
        h, w = bgr.shape[:2]
        hh, ww = (h + 31) // 32 * 32, (w + 31) // 32 * 32
        pad = cv2.copyMakeBorder(bgr, 0, hh - h, 0, ww - w, cv2.BORDER_REFLECT_101)
        x = np.ascontiguousarray(pad[..., ::-1].transpose(2, 0, 1), dtype=np.float32)[None] / 255.0
        acc = None
        for flip in (None, 3, 2):
            xi = x if flip is None else np.ascontiguousarray(np.flip(x, flip))
            o = 1.0 / (1.0 + np.exp(-self.sess.run(None, {self.inp: xi})[0]))
            if flip is not None:
                o = np.flip(o, flip)
            acc = o if acc is None else acc + o
        o = acc[0] / 3.0
        return o[0, :h // 2, :w // 2], o[1, :h // 2, :w // 2]

    def check(self, bgr: np.ndarray, quads: Sequence[np.ndarray], *, ref_side: Optional[int] = None
              ) -> Optional[RidgeCheck]:
        """bgr 좌표의 칸들을 확인. ref_side = 원본 사진의 긴 변(돌리거나 자르기 전) — 배율을 학습과 맞춘다."""
        qs = [np.asarray(q, np.float64).reshape(4, 2) for q in quads if q is not None]
        if len(qs) < MIN_TUBES:
            return None
        s = self.max_side / float(ref_side or max(bgr.shape[:2]))
        img = cv2.resize(bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
        body, ridge = self.maps(img)
        k = s / STRIDE
        segs = [(a * k, b * k, wd * k) for a, b, wd in (quad_seg(q) for q in qs)]
        res = decide(ridge, body, segs)
        res.added = [(a / k, b / k, wd / k) for a, b, wd in res.added]
        return res


_LOCK = threading.Lock()
_CACHE: dict = {}


def default_checker() -> Optional[RidgeChecker]:
    """AIVIS_RIDGE_MODEL 의 모델 — 없거나 실패하면 None(확인 끔). 경로마다 한 번만 연다."""
    path = os.environ.get("AIVIS_RIDGE_MODEL", "").strip()
    if not path:
        return None
    with _LOCK:
        if path not in _CACHE:
            try:
                _CACHE[path] = RidgeChecker(path) if Path(path).is_file() else None
            except Exception:  # noqa: BLE001 — onnxruntime 미설치·손상 파일: 확인만 끈다
                _CACHE[path] = None
        return _CACHE[path]
