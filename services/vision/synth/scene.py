"""다발 장면 합성 + 정답.

튜브 패치(tube.render_tube 또는 현장 사진에서 자른 real 패치)를 다발로 배치한다.

- 배치: 붙음(touching) / 벌어짐(gap) / 섞임(mixed) / 떨어짐(separated), 끝 엇갈림, 튜브마다 길이 편차
  (공차 밖이면 LEN 라벨), 표면 불량(defects.paint_defects).
- 배경: dark(무광 검정 — 현장 권장), tray(밝은 회색 트레이: 벽·리브), floor(밝은 얼룩 바닥).
- 카메라: 회전, 사다리꼴 원근, 조명 기울기, 흐림, 잡음, JPEG.
- 기준자: 튜브와 나란한 눈금 띠(선택).

정답(json): 개수, 튜브마다 네 모서리(최종 영상 좌표)·중심·길이(px·mm)·지름·불량 코드·경계 여부,
그리고 장면 조건. 길이 mm 는 물리 길이다 — 원근이 있으면 영상 px 길이는 위치마다 다르므로
길이 평가는 원근 없는 장면에서만 한다(eval 에서 걸러낸다).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

from .defects import DEFECT_CODES, paint_defects
from .tube import TubeLook, TubePatch, render_tube


@dataclass
class SceneSpec:
    """장면 조건(정답에 그대로 남긴다)."""

    width: int = 1600
    height: int = 1100
    background: str = "dark"          # dark | tray | floor
    n_tubes: int = 19
    length_mm: float = 430.0
    diameter_mm: float = 17.0
    mm_per_px: float = 0.354          # 60cm·카메라 모듈 3 절반 해상도(전체 해상도 0.177)
    arrangement: str = "touching"     # touching | gap | mixed | separated
    stagger_frac: float = 0.03        # 끝 엇갈림(길이 비)
    length_sigma_mm: float = 0.05     # 튜브마다 길이 편차 1σ
    len_defect_rate: float = 0.0      # 공차 밖 길이(LEN) 비율
    tol_mm: float = 0.5
    defect_rate: float = 0.0          # 표면 불량 튜브 비율
    border_rate: float = 0.0          # 그중 경계(옅은) 비율
    angle_deg: float = 0.0
    keystone: float = 0.0             # 사다리꼴 원근(위쪽이 좁아지는 비율, 0=없음)
    light_gradient: float = 0.1
    blur_sigma: float = 0.0
    noise: float = 2.0
    jpeg_quality: int = 92
    ruler: bool = False
    end_faces: bool = False           # 끝면(구멍)이 보이는가 — 비스듬히 찍을 때만. 위에서 수직이면 끝면은 카메라 반대쪽을 본다
    bg_level: Optional[float] = None  # 어두운 바닥 밝기(없으면 18~55 무작위) — 무광 검정이 덜 검은 현장
    dust: int = 0                     # 어두운 바닥의 먼지·부스러기 점 개수
    shift: Tuple[float, float] = (0.0, 0.0)   # 다발 중심 이동(화면 비)
    seed: int = 0

    @property
    def has_perspective(self) -> bool:
        return self.keystone > 1e-6


@dataclass
class TubeTruth:
    index: int
    quad: List[List[float]]
    center: List[float]
    length_px: float
    length_mm: float
    diameter_px: float
    labels: List[str] = field(default_factory=list)
    border: bool = False


# ------------------------------------------------------------------ 배경
def _background(spec: SceneSpec, rng: np.random.Generator, bundle_box) -> np.ndarray:
    h, w = spec.height, spec.width
    if spec.background == "dark":
        base = rng.uniform(18, 55) if spec.bg_level is None else float(spec.bg_level)
        img = np.full((h, w), base, np.float32)
        img += cv2.GaussianBlur(rng.normal(0, 3.0, (h, w)).astype(np.float32), (0, 0), 1.2)
        for _ in range(int(spec.dust)):           # 먼지·알루미늄 부스러기(밝거나 어두운 작은 점)
            cx, cy = int(rng.uniform(0, w)), int(rng.uniform(0, h))
            r = int(rng.integers(1, 5))
            cv2.circle(img, (cx, cy), r, float(np.clip(base + rng.choice([-1, 1]) * rng.uniform(40, 140), 0, 255)), -1)
        return np.dstack([img] * 3)
    # 밝은 얼룩 바닥(floor) — tray 의 바깥에도 쓴다
    base = rng.uniform(165, 205)
    floor = np.full((h, w), base, np.float32)
    floor += cv2.GaussianBlur(rng.normal(0, 12.0, (h, w)).astype(np.float32), (0, 0), 1.3)
    specks = rng.random((h, w)) < 0.003
    floor[specks] -= rng.uniform(30, 70)
    step = int(rng.uniform(0.3, 0.6) * max(h, w))
    off = int(rng.uniform(0, step))
    for x in range(off, w, step):
        floor[:, x:x + 2] -= 30
    for y in range(off, h, step):
        floor[y:y + 2, :] -= 30
    floor += np.linspace(-15, 15, w)[None, :] * rng.uniform(-1, 1)
    if spec.background == "floor":
        return np.dstack([floor] * 3)
    # tray: 다발을 둘러싼 밝은 플라스틱 판 + 벽(어두운 띠) + 리브(가는 선)
    x0, y0, x1, y1 = bundle_box
    mx, my = rng.uniform(0.06, 0.18) * (x1 - x0), rng.uniform(0.15, 0.6) * (y1 - y0)
    tx0, ty0, tx1, ty1 = int(x0 - mx), int(y0 - my), int(x1 + mx), int(y1 + my)
    img = floor.copy()
    tray = rng.uniform(190, 218)
    wall = int(max(8, 0.04 * min(tx1 - tx0, ty1 - ty0)))
    img[max(0, ty0 - wall):min(h, ty1 + wall), max(0, tx0 - wall):min(w, tx1 + wall)] = tray - rng.uniform(25, 45)
    img[max(0, ty0):min(h, ty1), max(0, tx0):min(w, tx1)] = tray
    img[max(0, ty0):min(h, ty1), max(0, tx0):min(w, tx1)] += cv2.GaussianBlur(
        rng.normal(0, 2.0, (max(1, min(h, ty1) - max(0, ty0)), max(1, min(w, tx1) - max(0, tx0)))).astype(np.float32),
        (0, 0), 1.0)
    # 리브: 벽에서 안쪽으로 짧은 가는 선
    for _ in range(int(rng.integers(4, 10))):
        if rng.random() < 0.5:
            x = int(rng.uniform(tx0, tx1))
            yy = (ty0 - wall, ty0 + wall) if rng.random() < 0.5 else (ty1 - wall, ty1 + wall)
            img[max(0, yy[0]):min(h, yy[1]), max(0, x):min(w, x + 2)] -= 35
        else:
            y = int(rng.uniform(ty0, ty1))
            xx = (tx0 - wall, tx0 + wall) if rng.random() < 0.5 else (tx1 - wall, tx1 + wall)
            img[max(0, y):min(h, y + 2), max(0, xx[0]):min(w, xx[1])] -= 35
    return np.dstack([img] * 3)


# ------------------------------------------------------------------ 배치
def _offsets(spec: SceneSpec, rng: np.random.Generator, D: float) -> List[float]:
    ys = [0.0]
    for _ in range(spec.n_tubes - 1):
        a = spec.arrangement
        if a == "touching":
            g = rng.uniform(-0.01, 0.02) * D
        elif a == "gap":
            g = rng.uniform(0.1, 0.8) * D
        elif a == "separated":
            g = rng.uniform(1.0, 3.0) * D
        else:  # mixed
            g = rng.uniform(0.1, 0.6) * D if rng.random() < 0.3 else rng.uniform(-0.01, 0.02) * D
        ys.append(ys[-1] + D + g)
    mid = 0.5 * (ys[0] + ys[-1])
    return [y - mid for y in ys]


def _composite(canvas: np.ndarray, patch: TubePatch, M: np.ndarray, *, shadow: float = 0.0,
               shadow_off: Tuple[float, float] = (0.0, 0.0)) -> None:
    """패치를 아핀 M(패치→장면)으로 canvas 에 합성(영역만 계산)."""
    rows, cols = patch.alpha.shape
    corners = np.array([[0, 0, 1], [cols - 1, 0, 1], [cols - 1, rows - 1, 1], [0, rows - 1, 1]], np.float64).T
    pts = M @ corners
    H, W = canvas.shape[:2]
    pad = 6 + int(abs(shadow_off[0]) + abs(shadow_off[1]))
    bx0, by0 = int(max(0, np.floor(pts[0].min()) - pad)), int(max(0, np.floor(pts[1].min()) - pad))
    bx1, by1 = int(min(W, np.ceil(pts[0].max()) + pad)), int(min(H, np.ceil(pts[1].max()) + pad))
    if bx1 <= bx0 or by1 <= by0:
        return
    Mb = M.copy()
    Mb[0, 2] -= bx0
    Mb[1, 2] -= by0
    size = (bx1 - bx0, by1 - by0)
    a = cv2.warpAffine(patch.alpha, Mb, size, flags=cv2.INTER_LINEAR, borderValue=0)[..., None]
    region = canvas[by0:by1, bx0:bx1]
    if shadow > 0:
        Ms = Mb.copy()
        Ms[0, 2] += shadow_off[0]
        Ms[1, 2] += shadow_off[1]
        s = cv2.warpAffine(patch.alpha, Ms, size, flags=cv2.INTER_LINEAR, borderValue=0)
        s = cv2.GaussianBlur(s, (0, 0), max(1.0, 0.25 * patch.diameter))[..., None]
        region *= (1.0 - shadow * s)
    rgb = cv2.warpAffine(patch.rgb, Mb, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    canvas[by0:by1, bx0:bx1] = region * (1 - a) + rgb * a


def _ruler(canvas: np.ndarray, spec: SceneSpec, rng: np.random.Generator, R: np.ndarray, c: np.ndarray,
           y_local: float, length_px: float, D: float, dark: bool) -> dict:
    """튜브와 나란한 눈금 띠(10mm 간격). 반환: 정답(띠 네 모서리, 눈금 수)."""
    pitch_px = 10.0 / spec.mm_per_px
    n_marks = int(length_px / pitch_px)
    hgt = max(8.0, 0.6 * D)
    W = n_marks * pitch_px + pitch_px
    strip = np.full((int(np.ceil(hgt)) + 2, int(np.ceil(W)) + 2), 235.0 if dark else 60.0, np.float32)
    mark = 25.0 if dark else 230.0
    for k in range(n_marks + 1):
        x = int(round(0.5 * pitch_px + k * pitch_px))
        strip[2:-2, max(0, x - 1):x + 1] = mark
    rows, cols = strip.shape
    patch = TubePatch(rgb=np.dstack([strip] * 3), alpha=np.ones_like(strip), un=np.zeros_like(strip),
                      vn=np.zeros_like(strip), length=float(W), diameter=float(hgt), pad=1)
    cp = np.array([(cols - 1) / 2.0, (rows - 1) / 2.0])
    center = c + R @ np.array([0.0, y_local])
    M = np.hstack([R, (center - R @ cp)[:, None]])
    _composite(canvas, patch, M)
    quad = (M @ np.array([[0, 0, 1], [cols - 1, 0, 1], [cols - 1, rows - 1, 1], [0, rows - 1, 1]], np.float64).T).T
    return {"quad": quad.tolist(), "pitch_mm": 10.0, "marks": n_marks + 1}


def make_scene(spec: SceneSpec, *, patch_source: Optional[Callable] = None):
    """장면 1장 → (BGR uint8, 정답 dict). patch_source(rng, length_px, diameter_px) 가 있으면 그 패치(실사 질감)."""
    rng = np.random.default_rng(spec.seed)
    mmpx = spec.mm_per_px
    L0, D = spec.length_mm / mmpx, spec.diameter_mm / mmpx
    ys = _offsets(spec, rng, D)
    # 다발이 화면에 들어오게(넘치면 카메라를 멀리 = mm/px 증가)
    ang = np.radians(spec.angle_deg)
    R = np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]])
    y_hi = ys[-1] + (2.4 * D if spec.ruler else D)      # 기준자 띠까지 화면에
    ext_local = np.array([[sx * (0.5 + spec.stagger_frac) * L0 * 1.12, sy] for sx in (-1, 1)
                          for sy in (ys[0] - D, y_hi)])
    ext = ext_local @ R.T
    span = ext.max(axis=0) - ext.min(axis=0)
    s = min(1.0, 0.86 * spec.width / span[0], 0.86 * spec.height / span[1])
    if s < 1.0:
        mmpx = mmpx / s
        L0, D = L0 * s, D * s
        ys = [y * s for y in ys]
    ext = (ext_local * np.array([s, s])) @ R.T if s < 1.0 else ext
    lo, hi = ext.min(axis=0), ext.max(axis=0)
    free = np.maximum(0.0, np.array([spec.width, spec.height]) * 0.93 - (hi - lo))
    c = (np.array([spec.width / 2, spec.height / 2]) - 0.5 * (lo + hi)
         + 0.5 * free * np.clip(np.array(spec.shift, float), -1, 1))
    corners = (np.array([[sx * 0.55 * L0, y] for sx in (-1, 1) for y in (ys[0] - D / 2, ys[-1] + D / 2)]) @ R.T) + c
    box = (corners[:, 0].min(), corners[:, 1].min(), corners[:, 0].max(), corners[:, 1].max())
    canvas = _background(spec, rng, box).astype(np.float32)
    light = spec.background != "dark"
    truths: List[TubeTruth] = []
    ruler = None
    if spec.ruler:
        ruler = _ruler(canvas, spec, rng, R, c, ys[-1] + 1.6 * D, L0 * 1.1, D, dark=not light)
    shadow_dir = rng.uniform(0, 2 * np.pi)
    scene_look = TubeLook.random(rng, light_bg=light)      # 다발 전체가 같은 조명을 받는다
    for i, y in enumerate(ys):
        dev_mm = rng.normal(0, spec.length_sigma_mm)
        labels: List[str] = []
        if rng.random() < spec.len_defect_rate:
            dev_mm = float(rng.choice([-1, 1]) * rng.uniform(1.3, 4.0) * spec.tol_mm)
            labels.append("LEN")
        L = (spec.length_mm + dev_mm) / mmpx
        if patch_source is not None:
            patch = patch_source(rng, L, D)
        else:
            look = scene_look.jitter(rng)
            if not spec.end_faces:
                look.bore_end = 0
            patch = render_tube(L, D, rng, look=look, light_bg=light)
        border = False
        if rng.random() < spec.defect_rate:
            k = 2 if rng.random() < 0.15 else 1
            codes = list(rng.choice(DEFECT_CODES, size=k, replace=False))
            border = bool(rng.random() < spec.border_rate)
            labels = labels + paint_defects(patch, codes, rng, border=border)
        rows, cols = patch.alpha.shape
        cp = np.array([patch.pad + patch.length / 2.0, (rows - 1) / 2.0])
        x_off = rng.uniform(-spec.stagger_frac, spec.stagger_frac) * L0
        center = c + R @ np.array([x_off, y])
        M = np.hstack([R, (center - R @ cp)[:, None]])
        _composite(canvas, patch, M, shadow=0.18 if light else 0.0,
                   shadow_off=(0.08 * D * np.cos(shadow_dir), 0.08 * D * np.sin(shadow_dir)))
        q = np.array([[patch.pad, cp[1] - D / 2, 1], [patch.pad + patch.length, cp[1] - D / 2, 1],
                      [patch.pad + patch.length, cp[1] + D / 2, 1], [patch.pad, cp[1] + D / 2, 1]], np.float64)
        quad = (M @ q.T).T
        truths.append(TubeTruth(index=i + 1, quad=quad.tolist(), center=center.tolist(), length_px=float(patch.length),
                                length_mm=float(patch.length * mmpx), diameter_px=float(D), labels=labels, border=border))
    # 조명 기울기
    h, w = spec.height, spec.width
    g = spec.light_gradient
    gx = np.linspace(1 - g, 1 + g, w)[None, :] if rng.random() < 0.5 else np.linspace(1 + g, 1 - g, w)[None, :]
    gy = np.linspace(1 - g / 2, 1 + g / 2, h)[:, None]
    canvas *= (gx * gy)[..., None]
    Hm = np.eye(3)
    if spec.has_perspective:
        k = spec.keystone
        src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        dst = np.float32([[k * w, 0], [w - k * w, 0], [w, h], [0, h]])
        Hm = cv2.getPerspectiveTransform(src, dst).astype(np.float64)
        canvas = cv2.warpPerspective(canvas, Hm, (w, h), borderMode=cv2.BORDER_REFLECT_101)

        def warp(p):
            a = np.c_[np.asarray(p, np.float64), np.ones(len(p))] @ Hm.T
            return (a[:, :2] / a[:, 2:3]).tolist()
        for t in truths:
            t.quad = warp(t.quad)
            t.center = warp([t.center])[0]
        if ruler:
            ruler["quad"] = warp(ruler["quad"])
    if spec.blur_sigma > 0:
        canvas = cv2.GaussianBlur(canvas, (0, 0), spec.blur_sigma)
    canvas += rng.normal(0, spec.noise, canvas.shape).astype(np.float32)
    img = np.clip(canvas, 0, 255).astype(np.uint8)
    if spec.jpeg_quality < 100:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(spec.jpeg_quality)])
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    gt = {
        "spec": asdict(spec),
        "mm_per_px": float(mmpx),
        "n_tubes": len(truths),
        "tubes": [asdict(t) for t in truths],
        "ruler": ruler,
        "homography": Hm.tolist(),
        "synthetic": True,
        "note": "직접 만든 데이터 — 인수 증빙 아님",
    }
    return img, gt


def random_spec(rng: np.random.Generator, *, kind: str, seed: int, size: Tuple[int, int] = (1600, 1100)) -> SceneSpec:
    """평가·학습용 조건 묶음.

    production : 현장 확정 조건 — 무광 검정, 위에서 수직(원근 없음), 거의 정렬(±3°), 기준자, 붙음/약간 벌어짐.
    tray       : 밝은 트레이 위 붙은 다발(사진 시험 조건) — 회전·원근 있음.
    floor      : 밝은 바닥 위 떨어진 튜브 몇 개.
    hard       : 강한 원근·큰 회전·흐림·저대비.
    field      : production 에서 현장에서 흔히 벗어나는 것들(약한 원근·덜 검은 바닥·조명 기울기·흐림·먼지·큰 다발).
    """
    w, h = size
    n = int(rng.integers(8, 26))
    common = dict(width=w, height=h, seed=seed, length_mm=float(rng.uniform(250, 450)),
                  diameter_mm=float(rng.uniform(12, 22)), noise=float(rng.uniform(1.0, 3.5)),
                  jpeg_quality=int(rng.integers(80, 96)), defect_rate=0.15, border_rate=0.3,
                  len_defect_rate=0.08, length_sigma_mm=0.05, tol_mm=0.5)
    if kind == "production":
        return SceneSpec(background="dark", n_tubes=n, arrangement=str(rng.choice(["touching", "touching", "mixed", "gap"])),
                         stagger_frac=float(rng.uniform(0.0, 0.04)), angle_deg=float(rng.uniform(-3, 3)),
                         keystone=0.0, light_gradient=float(rng.uniform(0.0, 0.2)), blur_sigma=float(rng.uniform(0, 0.8)),
                         ruler=True, shift=(float(rng.uniform(-1, 1)), float(rng.uniform(-1, 1))),
                         mm_per_px=0.354, **common)
    if kind == "field":
        # 현장 조건에서 흔히 벗어나는 것들: 카메라가 조금 기욺(약한 원근), 덜 검은 바닥, 한쪽으로 기운 조명,
        # 초점 흐림, 먼지·부스러기, 큰 다발(최대 40개), ±5°, 기준자 없는 경우.
        return SceneSpec(background="dark", n_tubes=int(rng.integers(5, 41)),
                         arrangement=str(rng.choice(["touching", "touching", "mixed", "gap"])),
                         stagger_frac=float(rng.uniform(0.0, 0.05)), angle_deg=float(rng.uniform(-5, 5)),
                         keystone=float(rng.choice([0.0, rng.uniform(0.005, 0.025)])),
                         light_gradient=float(rng.uniform(0.0, 0.35)), blur_sigma=float(rng.uniform(0, 1.2)),
                         ruler=bool(rng.random() < 0.7), shift=(float(rng.uniform(-1, 1)), float(rng.uniform(-1, 1))),
                         bg_level=float(rng.uniform(15, 85)), dust=int(rng.integers(0, 60)), mm_per_px=0.354, **common)
    if kind == "tray":
        sp = SceneSpec(background="tray", n_tubes=n, arrangement=str(rng.choice(["touching", "touching", "mixed"])),
                       stagger_frac=float(rng.uniform(0.0, 0.05)), angle_deg=float(rng.uniform(-50, 50)),
                       keystone=float(rng.choice([0.0, rng.uniform(0.03, 0.15)])),
                       light_gradient=float(rng.uniform(0.05, 0.3)), blur_sigma=float(rng.uniform(0, 1.0)),
                       mm_per_px=0.354, **common)
        sp.end_faces = sp.has_perspective
        return sp
    if kind == "floor":
        common["length_mm"] = float(rng.uniform(250, 450))
        sp = SceneSpec(background="floor", n_tubes=int(rng.integers(3, 9)), arrangement="separated",
                       stagger_frac=float(rng.uniform(0.0, 0.1)), angle_deg=float(rng.uniform(-60, 60)),
                       keystone=float(rng.choice([0.0, rng.uniform(0.03, 0.12)])),
                       light_gradient=float(rng.uniform(0.05, 0.3)), blur_sigma=float(rng.uniform(0, 1.0)),
                       mm_per_px=0.354, **common)
        sp.end_faces = sp.has_perspective
        return sp
    if kind == "hard":
        return SceneSpec(background=str(rng.choice(["tray", "floor", "dark"])), n_tubes=n,
                         arrangement=str(rng.choice(["touching", "mixed"])), stagger_frac=float(rng.uniform(0.03, 0.08)),
                         angle_deg=float(rng.uniform(-60, 60)), keystone=float(rng.uniform(0.12, 0.25)),
                         light_gradient=float(rng.uniform(0.2, 0.4)), blur_sigma=float(rng.uniform(0.8, 1.8)),
                         end_faces=True, mm_per_px=0.354, **common)
    raise ValueError(f"모르는 장면 종류: {kind}")


__all__ = ["SceneSpec", "TubeTruth", "make_scene", "random_spec"]
