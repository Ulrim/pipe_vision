"""끝단 서브픽셀 추정기 벤치마크 — 길이 측정 S자 치우침(2026-10-20).

**무엇을 재는가.** 튜브 끝단(밝기 계단)의 화소 사이 위치를 정수 극값 주변 몇 화소로
추정하는 방법들을, 끝단이 화소 안 어디에 걸리느냐(위상 0~1)에 따라 얼마나 치우치는지
(bias), 노이즈에 얼마나 흔들리는지(std), 엉뚱한 답(|오차|>1px)을 내는지로 비교한다.
치우침은 반복 측정으로 안 보인다(%GR&R 에 안 잡힌다) — 기준 시료가 없으면 현장에서는
못 찾는다. 그래서 정답을 아는 합성으로 잰다.

합성(결정적, 시드 고정):
  * 1D 계단 — 화소 면적 적분(화소 k 가 [k-0.5, k+0.5] 를 덮는다)한 가우시안 블러 계단을
    **해석적으로** 그린다(erf 적분 — 보간·업샘플 오차 없음). 배경 30·제품 185(gen_synthetic 과
    같은 밝기), 8비트 양자화. 상승·하강 둘 다. 블러 σ 0(참고: 시험 합성 조건)·0.5·0.8·1.2·2.0px,
    노이즈 0·2·5 계조.
  * 테두리 반짝임(rim) — 끝단 안쪽 첫 1px 이 평탄부보다 +25(gen_synthetic 끝단 선 210 vs 몸체
    185) 또는 +60(같은 선이 음영으로 어두워진 몸체 위에 있을 때) 밝다. 끝단 버·모따기 반사.
  * 2D 튜브 — 같은 방식으로 그린 막대를 `measure_length_ex`(세로 평균 + 행별 직선 적합)와
    `InspectionPipeline`(전처리 포함)에 태워 **길이** 오차를 잰다. 두 끝단 위상이 서로 다르게 돈다.

후보(모두 1D 밝기 신호 + 정수 극값 위치 → 서브픽셀 위치, 순수 함수):
  parabola_smooth     현행 세로평균 경로 — 3점 평활 기울기 |grad| 3점 포물선
  parabola_raw        현행 행별 경로 — 중앙차분 기울기 3점 포물선
  gauss_smooth        평활 기울기 로그-포물선(가우시안 정점)
  centroid            중앙차분 기울기 무게중심(정수 극값 ±2 고정 창)
  mid50_linear        좌우 레벨(중앙값) 중간 밝기 교차점, 선형 보간 1회
  mid50_symmetric     위 + 추정 끝단 기준 대칭 재읽기 2회
  mid50_med_box3      위를 3점 중앙값 → 3점 평균 신호에서(차점)
  area_adaptive       부분면적(모멘트) — 레벨 사이 면적으로 위치, 창은 끝단 폭에 맞춘다
  esf_fit             erf 계단 모델 4변수 가우스-뉴턴 적합(기준용 — 느려서 시드 1/4)
  centroid_symmetric  3점 중앙값 → 기울기 → 추정 중심 대칭 창(±3px) 무게중심 — **채택**
                      (vision.length.subpixel)

끝단-끝단 파이프라인은 두 입력으로 잰다 — 반사 보정본에서 위치까지(2026-10-20 이전) /
선형 그레이에서 위치(이후). 보정본의 끝단 옆 후광 + 미디언이 어떤 추정기로도 못 되돌리는
오차를 만든다(결과 문서 참조).

사용:
    python -m vision.tools.subpixel_benchmark [--seeds 100] [--quick] [--md out.md] [--json out.json]
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import math
import sys
import time
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence

import numpy as np

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from aivis_types import ItemMaster  # noqa: E402

import vision.length.edges as _edges_mod  # noqa: E402
import vision.length.measure as _measure_mod  # noqa: E402
import vision.pipeline as _pipeline_mod  # noqa: E402
from vision.length.measure import (  # noqa: E402
    _parabolic_subpixel,
    _smooth_gradient,
    measure_length_ex,
)
from vision.length.subpixel import edge_subpixel, edge_subpixel_rows  # noqa: E402

Estimator = Callable[[np.ndarray, int], Optional[float]]

BG, FG = 30.0, 185.0                      # gen_synthetic BG_GRAY / PIPE_GRAY
RIMS = (0.0, 25.0, 60.0)
PHASES = np.round(np.arange(0.0, 1.0 + 1e-9, 0.05), 2)
BLURS = (0.0, 0.5, 0.8, 1.2, 2.0)         # 0.0 = 블러 없음(시험 합성 조건, 참고)
NOISES = (0.0, 2.0, 5.0)
FAIL_PX = 1.0                             # 이보다 크게 틀리면 '실패'(치우침 계산에서 뺀다)
CHOSEN = "centroid_symmetric"
#: 현행 = 세로평균은 parabola_smooth, 행별은 parabola_raw. 끝단-끝단의 '현행' 은 이 조합이다.
CURRENT = "current"


# ---- 해석적 렌더링 -----------------------------------------------------------
def _erf(x: np.ndarray) -> np.ndarray:
    """Abramowitz–Stegun 7.1.26 (최대 오차 1.5e-7). scipy 없이 numpy 만으로."""
    x = np.asarray(x, dtype=np.float64)
    a = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * a)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                - 0.284496736) * t + 0.254829592) * t * np.exp(-a * a)
    return np.sign(x) * y


def _Phi(u):
    return 0.5 * (1.0 + _erf(np.asarray(u) / math.sqrt(2.0)))


def _phi(u):
    u = np.asarray(u, dtype=np.float64)
    return np.exp(-0.5 * u * u) / math.sqrt(2.0 * math.pi)


def box_step(t: np.ndarray, sigma: float) -> np.ndarray:
    """화소 중심 오프셋 t 에서, 0 에 있는 단위 계단을 블러(σ) 후 화소 면적 적분한 값."""
    t = np.asarray(t, dtype=np.float64)
    if sigma <= 0:
        return np.clip(t + 0.5, 0.0, 1.0)

    def F(u):  # ∫Φ
        return u * _Phi(u) + _phi(u)

    return sigma * (F((t + 0.5) / sigma) - F((t - 0.5) / sigma))


def bar_profile(x: np.ndarray, left: float, right: float, sigma: float, *,
                rim: float = 0.0) -> np.ndarray:
    """[left, right] 의 밝은 막대(배경 BG, 제품 FG). rim 이면 양 끝 안쪽 1px 이 더 밝다."""
    p = (FG - BG) * (box_step(x - left, sigma) - box_step(x - right, sigma))
    if rim:
        p += rim * (box_step(x - left, sigma) - box_step(x - left - 1.0, sigma))
        p += rim * (box_step(x - right + 1.0, sigma) - box_step(x - right, sigma))
    return BG + p


def edge_signal(pos: float, sigma: float, *, rising: bool, rim: float = 0.0,
                n: int = 41) -> np.ndarray:
    """1D 끝단 하나. rising 이면 왼쪽 배경 → 오른쪽 제품."""
    x = np.arange(n, dtype=np.float64)
    if rising:
        return bar_profile(x, pos, n + 100.0, sigma, rim=rim)
    return bar_profile(x, -100.0, pos, sigma, rim=rim)


def _quantize(v: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(v), 0, 255)


# ---- 후보 추정기(스칼라) ------------------------------------------------------
def _polarity(s: np.ndarray, i: int) -> float:
    a, b = max(i - 1, 0), min(i + 1, s.size - 1)
    return 1.0 if s[b] >= s[a] else -1.0


def _local_peak(g: np.ndarray, i: int) -> int:
    lo, hi = max(i - 1, 0), min(i + 2, g.size)
    return lo + int(np.argmax(g[lo:hi]))


def est_parabola_smooth(s: np.ndarray, i: int) -> Optional[float]:
    """현행 세로평균 경로(measure._find_edges 2026-10-17 판)."""
    _sm, g = _smooth_gradient(s)
    j = _local_peak(g * _polarity(s, i), i)
    return _parabolic_subpixel(np.abs(g), j)


def est_parabola_raw(s: np.ndarray, i: int) -> Optional[float]:
    """현행 행별 경로(edges._row_subpixel 2026-10-17 판 그대로)."""
    g = np.gradient(np.asarray(s, dtype=np.float64)) * _polarity(s, i)
    if i <= 0 or i >= g.size - 1:
        return None
    y0, y1, y2 = float(g[i - 1]), float(g[i]), float(g[i + 1])
    denom = y0 - 2.0 * y1 + y2
    if abs(denom) < 1e-9:
        return float(i)
    delta = 0.5 * (y0 - y2) / denom
    if abs(delta) > 1.0:
        return float(i)
    return float(i) + delta


def est_gauss_smooth(s: np.ndarray, i: int) -> Optional[float]:
    """평활 기울기 3점의 로그에 포물선 — 가우시안 정점(블러가 가우시안이면 정확)."""
    _sm, g = _smooth_gradient(s)
    g = g * _polarity(s, i)
    j = _local_peak(g, i)
    if j <= 0 or j >= g.size - 1:
        return None
    l0, l1, l2 = np.log(np.maximum(g[j - 1:j + 2], 1e-3))
    denom = l0 - 2.0 * l1 + l2
    if abs(denom) < 1e-12:
        return float(j)
    delta = 0.5 * (l0 - l2) / denom
    return float(j) + delta if abs(delta) <= 1.0 else float(j)


def est_centroid(s: np.ndarray, i: int, half: int = 2) -> Optional[float]:
    """중앙차분 기울기의 무게중심(정수 극값 ±half 고정 창, 반대 부호는 0)."""
    g = np.gradient(np.asarray(s, dtype=np.float64)) * _polarity(s, i)
    lo, hi = max(0, i - half), min(g.size, i + half + 1)
    w = np.clip(g[lo:hi], 0.0, None)
    if w.sum() <= 0:
        return None
    return float(np.dot(np.arange(lo, hi), w) / w.sum())


def _med_at(s: np.ndarray, idx: Sequence[int]) -> float:
    k = np.clip(np.asarray(idx), 0, s.size - 1)
    return float(np.median(s[k]))


def _interp(s: np.ndarray, x: float) -> float:
    k = min(max(int(math.floor(x)), 0), s.size - 2)
    f = min(max(x - k, 0.0), 1.0)
    return float(s[k] * (1.0 - f) + s[k + 1] * f)


def _cross_near(s: np.ndarray, c: int, mid: float, pol: float, reach: int = 2) -> Optional[float]:
    """c 주변에서 mid 를 pol 방향으로 지나는 곳(선형 보간) 중 c 에 가장 가까운 것."""
    best, best_d = None, math.inf
    for k in range(c - reach, c + reach):
        if k < 0 or k + 1 >= s.size:
            continue
        a, b = s[k] - mid, s[k + 1] - mid
        if a * b > 0 or (b - a) * pol <= 0:
            continue
        x = k + a / (a - b)
        if abs(x - c) < best_d:
            best, best_d = x, abs(x - c)
    return best


def est_mid50_linear(s: np.ndarray, i: int) -> Optional[float]:
    """정수 끝단 ±(2~4)px 중앙값 레벨의 중간 밝기 교차점, 선형 보간 1회."""
    s = np.asarray(s, dtype=np.float64)
    left = _med_at(s, [i - 2, i - 3, i - 4])
    right = _med_at(s, [i + 2, i + 3, i + 4])
    if abs(right - left) < 1e-6:
        return None
    return _cross_near(s, i, 0.5 * (left + right), math.copysign(1.0, right - left))


def est_mid50_symmetric(s: np.ndarray, i: int, iters: int = 2, gap: float = 3.0) -> Optional[float]:
    """mid50_linear 후, 추정 끝단에서 좌우 같은 거리(gap..gap+2)로 레벨을 다시 읽어 반복."""
    s = np.asarray(s, dtype=np.float64)
    x = est_mid50_linear(s, i)
    for _ in range(iters):
        if x is None:
            return None
        left = float(np.median([_interp(s, x - gap - j) for j in range(3)]))
        right = float(np.median([_interp(s, x + gap + j) for j in range(3)]))
        if abs(right - left) < 1e-6:
            return None
        c = min(max(int(round(x)), i - 2), i + 2)
        x = _cross_near(s, c, 0.5 * (left + right), math.copysign(1.0, right - left))
    return x


def _med3_box3(s: np.ndarray) -> np.ndarray:
    p = np.pad(np.asarray(s, dtype=np.float64), 1, mode="edge")
    m = np.median(np.stack([p[:-2], p[1:-1], p[2:]]), axis=0)
    p = np.pad(m, 1, mode="edge")
    return (p[:-2] + p[1:-1] + p[2:]) / 3.0


def est_mid50_med_box3(s: np.ndarray, i: int) -> Optional[float]:
    """3점 중앙값 → 3점 평균 후 mid50_symmetric. 평균이 면적 적분 계단을 정확한 3화소 직선
    경사로 만들어 선형 보간 치우침을 없앤다(중앙값은 1px 튐 제거)."""
    return est_mid50_symmetric(_med3_box3(s), i)


def est_area_adaptive(s: np.ndarray, i: int, *, k_width: float = 1.4,
                      n_min: int = 3, n_max: int = 12) -> Optional[float]:
    """부분면적(모멘트) — 창 안 면적이 끝단 위치에 선형이라는 성질(화소 면적 적분)을 쓴다.

    e = 창 중심 − Σ(s − 중간 밝기)/(우레벨 − 좌레벨). 대칭 블러면 창이 전이 구간을 덮을 때
    정확하다. 창 폭은 끝단 폭(대비 ÷ 최대 기울기)의 1.4배.
    """
    s = np.asarray(s, dtype=np.float64)
    x = est_mid50_linear(s, i)
    if x is None:
        return None
    g = np.abs(np.gradient(s))
    for _ in range(2):
        c0 = int(round(x))
        contrast = abs(_med_at(s, [c0 + 4, c0 + 5, c0 + 6]) - _med_at(s, [c0 - 4, c0 - 5, c0 - 6]))
        gmax = float(np.max(g[max(0, c0 - 2):c0 + 3]))
        if contrast < 1e-6 or gmax < 1e-6:
            return None
        n = int(min(n_max, max(n_min, math.ceil(k_width * contrast / gmax))))
        if n % 2:
            c = float(round(x))
            a = int(c) - n // 2
        else:
            c = math.floor(x) + 0.5
            a = int(c - 0.5) - (n // 2 - 1)
        b = a + n - 1
        left = _med_at(s, [a - 2, a - 3, a - 4])
        right = _med_at(s, [b + 2, b + 3, b + 4])
        if abs(right - left) < 1e-6:
            return None
        k = np.clip(np.arange(a, b + 1), 0, s.size - 1)
        x = c - float(np.sum(s[k] - 0.5 * (left + right))) / (right - left)
    return x


def est_esf_fit(s: np.ndarray, i: int, half: int = 6, iters: int = 8) -> Optional[float]:
    """erf 계단 모델 A + C·box_step(k − e; σ) 를 가우스-뉴턴(약한 감쇠)으로 적합."""
    s = np.asarray(s, dtype=np.float64)
    x0 = edge_subpixel(s, i)
    if x0 is None:
        return None
    k = np.arange(max(0, i - half), min(s.size, i + half + 1), dtype=np.float64)
    y = s[k.astype(int)]
    left = _med_at(s, [i - 4, i - 5, i - 6])
    right = _med_at(s, [i + 4, i + 5, i + 6])
    th = np.array([left, right - left, x0, 1.0])
    for _ in range(iters):
        a_, c_, e_, sg = th
        t = k - e_
        up, um = (t + 0.5) / sg, (t - 0.5) / sg
        b = box_step(t, sg)
        J = np.stack([np.ones_like(t), b, -c_ * (_Phi(up) - _Phi(um)),
                      c_ * (_phi(up) - _phi(um))], axis=1)
        r = y - (a_ + c_ * b)
        H = J.T @ J
        H[np.diag_indices(4)] *= 1.0 + 1e-3
        try:
            step = np.linalg.solve(H, J.T @ r)
        except np.linalg.LinAlgError:
            return None
        th = th + step
        th[3] = min(max(th[3], 0.05), 6.0)
        th[2] = min(max(th[2], x0 - 2.0), x0 + 2.0)
    return float(th[2])


def est_centroid_symmetric(s: np.ndarray, i: int) -> Optional[float]:
    """**운영 추정기** — vision.length.subpixel.edge_subpixel."""
    return edge_subpixel(s, i)


ESTIMATORS: Dict[str, Estimator] = {
    "parabola_smooth": est_parabola_smooth,
    "parabola_raw": est_parabola_raw,
    "gauss_smooth": est_gauss_smooth,
    "centroid": est_centroid,
    "mid50_linear": est_mid50_linear,
    "mid50_symmetric": est_mid50_symmetric,
    "mid50_med_box3": est_mid50_med_box3,
    "area_adaptive": est_area_adaptive,
    "esf_fit": est_esf_fit,
    CHOSEN: est_centroid_symmetric,
}
#: 느린 후보는 시드를 이만큼 나눠 쓴다(기준용이라 치우침 추정만 보면 된다).
SEED_DIVISOR = {"esf_fit": 4}
#: 끝단-끝단에 태울 구성. esf_fit 은 행마다 ms 단위라 뺀다.
E2E = (CURRENT, "gauss_smooth", "mid50_symmetric", "mid50_med_box3", "area_adaptive", CHOSEN)
PIPELINE = (CURRENT, "gauss_smooth", "mid50_med_box3", "area_adaptive", CHOSEN)


# ---- 1D 위상 스윕 --------------------------------------------------------------
@dataclasses.dataclass
class Stat:
    max_bias: float     # 위상·극성별 평균 오차의 최대 절댓값
    mean_bias: float    # 같은 것의 평균
    std: float          # 위상·극성별 표준편차의 RMS(노이즈 σ)
    fails: int          # |오차|>FAIL_PX 또는 추정 실패
    total: int


def _summarise(errs_by_case: List[List[float]], fails: int, total: int) -> Stat:
    bias, var = [], []
    for e in errs_by_case:
        if e:
            bias.append(float(np.mean(e)))
            var.append(float(np.var(e)))
    b = np.abs(np.array(bias)) if bias else np.array([np.nan])
    return Stat(max_bias=float(np.max(b)), mean_bias=float(np.mean(b)),
                std=float(np.sqrt(np.mean(var))) if var else float("nan"),
                fails=fails, total=total)


def sweep_1d(est: Estimator, sigma: float, noise: float, rim: float, seeds: int, *,
             quantize: bool = True) -> Stat:
    """위상 21점 × 상승·하강 × 시드. 정수 끝단은 중앙차분 극값(행별 경로와 같은 앞단).
    quantize=False 면 8비트 반올림 없이(노이즈 0 에서 추정기 자체의 S자만 남는다)."""
    rng = np.random.default_rng(20261020 + int(sigma * 100) + int(noise * 10) + int(rim))
    n_seed = max(1, seeds) if noise > 0 else 1
    cases, fails, total = [], 0, 0
    for rising in (True, False):
        for ph in PHASES:
            pos = 20.0 + float(ph)
            base = edge_signal(pos, sigma, rising=rising, rim=rim)
            errs = []
            for _ in range(n_seed):
                s = base + (rng.normal(0.0, noise, base.size) if noise else 0.0)
                s = _quantize(s) if quantize else s
                g = np.gradient(s) * (1.0 if rising else -1.0)
                i = 10 + int(np.argmax(g[10:31]))
                x = est(s, i)
                total += 1
                if x is None or not math.isfinite(x) or abs(x - pos) > FAIL_PX:
                    fails += 1
                    continue
                errs.append(x - pos)
            cases.append(errs)
    return _summarise(cases, fails, total)


# ---- 끝단-끝단(길이) -------------------------------------------------------------
def _rows_adapter(est: Estimator) -> Callable[[np.ndarray, np.ndarray], np.ndarray]:
    """스칼라 후보를 운영 훅(edge_subpixel_rows) 모양으로 감싼다."""
    def rows_fn(rows: np.ndarray, idx: np.ndarray) -> np.ndarray:
        out = np.full(len(idx), np.nan)
        for r, i in enumerate(np.asarray(idx)):
            x = est(np.asarray(rows[r], dtype=np.float64), int(i))
            if x is not None and math.isfinite(x):
                out[r] = x
        return out
    return rows_fn


@contextlib.contextmanager
def use_estimator(name: str, *, linear: bool = True) -> Iterator[None]:
    """측정 경로의 추정기를 잠시 바꾼다. linear=False 면 파이프라인이 반사 보정본에서
    위치까지 잰다(2026-10-20 이전 입력)."""
    saved = (_measure_mod.edge_subpixel_rows, _edges_mod.edge_subpixel_rows,
             _pipeline_mod.preprocess)
    if name == CURRENT:
        prof, rows = _rows_adapter(est_parabola_smooth), _rows_adapter(est_parabola_raw)
    elif name == CHOSEN:
        prof = rows = edge_subpixel_rows
    else:
        prof = rows = _rows_adapter(ESTIMATORS[name])
    _measure_mod.edge_subpixel_rows = prof
    _edges_mod.edge_subpixel_rows = rows
    if not linear:
        real = saved[2]

        def pre_no_linear(*a, **kw):
            return dataclasses.replace(real(*a, **kw), gray=None)
        _pipeline_mod.preprocess = pre_no_linear
    try:
        yield
    finally:
        (_measure_mod.edge_subpixel_rows, _edges_mod.edge_subpixel_rows,
         _pipeline_mod.preprocess) = saved


def _item(length_px: float) -> ItemMaster:
    return ItemMaster(item_code="SUBPX", item_name="bench", ref_length_mm=length_px,
                      tol_plus_mm=5.0, tol_minus_mm=5.0, px_to_mm_scale=1.0, expected_count=1)


def _lengths() -> List[tuple]:
    """(왼끝, 길이) — 길이 위상 0~1, 왼끝 위상은 0.37 보폭으로 따로 돈다."""
    return [(200.0 + (0.37 * j) % 1.0, 500.0 + float(ph)) for j, ph in enumerate(PHASES)]


def tube_window(left: float, length: float, sigma: float, noise: float, rim: float,
                rng: np.random.Generator, *, rows: int = 60) -> np.ndarray:
    """measure_length_ex 에 바로 넣는 창 — 운영 length_window 처럼 끝단 밖 배경 8px 포함."""
    x0 = int(math.floor(left)) - 8
    x = np.arange(x0, int(math.ceil(left + length)) + 9, dtype=np.float64)
    prof = bar_profile(x, left, left + length, sigma, rim=rim)
    img = np.repeat(prof[None, :], rows, axis=0)
    if noise:
        img = img + rng.normal(0.0, noise, img.shape)
    return _quantize(img).astype(np.uint8)


def tube_frame(left: float, length: float, sigma: float, noise: float, rim: float,
               rng: np.random.Generator, *, h: int = 200, w: int = 900) -> np.ndarray:
    """InspectionPipeline 에 넣는 BGR 프레임 — 세로로도 블러된 60행짜리 막대."""
    x = np.arange(w, dtype=np.float64)
    y = np.arange(h, dtype=np.float64)
    px = bar_profile(x, left, left + length, sigma, rim=rim) - BG
    py = box_step(y - 69.5, sigma) - box_step(y - 129.5, sigma)
    img = BG + py[:, None] * px[None, :]
    if noise:
        img = img + rng.normal(0.0, noise, img.shape)
    g = _quantize(img).astype(np.uint8)
    return np.repeat(g[:, :, None], 3, axis=2)


def length_sweep(name: str, sigma: float, noise: float, rim: float, seeds: int, *,
                 pipeline: bool, linear: bool = True) -> Stat:
    """길이 오차(px). 위상 21점 × 시드. 실패 = 끝단 미검출 또는 |오차|>2px."""
    rng = np.random.default_rng(1017 + int(sigma * 100) + int(noise * 10) + int(rim))
    n_seed = seeds if noise > 0 else 1
    pipe = _pipeline_mod.InspectionPipeline() if pipeline else None
    cases, fails, total = [], 0, 0
    with use_estimator(name, linear=linear):
        for left, length in _lengths():
            errs = []
            for _ in range(n_seed):
                total += 1
                if pipe is not None:
                    frame = tube_frame(left, length, sigma, noise, rim, rng)
                    meas = pipe.run(frame, _item(length)).length.meas_length_mm
                else:
                    win = tube_window(left, length, sigma, noise, rim, rng)
                    meas = measure_length_ex(win, _item(length))[0].meas_length_mm
                if meas is None or abs(meas - length) > 2.0 * FAIL_PX:
                    fails += 1
                    continue
                errs.append(meas - length)
            cases.append(errs)
    return _summarise(cases, fails, total)


# ---- 시간 ------------------------------------------------------------------------
def time_us(fn: Callable[[], object], n: int = 400) -> float:
    fn()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) / n * 1e6


def timings() -> Dict[str, float]:
    """1D 호출당 µs, 행 묶음 호출의 행당 µs, 튜브 하나(60행) measure_length_ex ms."""
    s = _quantize(edge_signal(20.3, 0.8, rising=True))
    out = {f"1d_us.{k}": time_us(lambda f=f: f(s, 20), 100 if k == "esf_fit" else 500)
           for k, f in ESTIMATORS.items()}
    for n_rows in (60, 170):
        rows = np.repeat(s[None, :], n_rows, axis=0)
        idx = np.full(n_rows, 20)
        out[f"row_us.{CHOSEN}.{n_rows}rows"] = time_us(
            lambda r=rows, i=idx: edge_subpixel_rows(r, i), 200) / n_rows
        out[f"row_us.parabola_raw.{n_rows}rows"] = time_us(
            lambda r=rows: [est_parabola_raw(r[k], 20) for k in range(n_rows)], 50) / n_rows
    win = tube_window(200.3, 500.4, 0.8, 2.0, 0.0, np.random.default_rng(0))
    for name in (CURRENT, CHOSEN):
        with use_estimator(name):
            out[f"tube60_ms.{name}"] = time_us(
                lambda: measure_length_ex(win, _item(500.4)), 100) / 1e3
    return out


# ---- 실행 ------------------------------------------------------------------------
def run(seeds: int, *, quick: bool = False) -> dict:
    blurs = (0.0, 0.8, 2.0) if quick else BLURS
    noises = (0.0, 5.0) if quick else NOISES
    res: dict = {"seeds": seeds, "phases": len(PHASES), "one_d_float": {}, "one_d": {},
                 "length": {}, "pipeline": {}, "timing": {}}
    for sigma in blurs:
        res["one_d_float"][f"σ{sigma}"] = {
            k: dataclasses.asdict(sweep_1d(f, sigma, 0.0, 0.0, 1, quantize=False))
            for k, f in ESTIMATORS.items()}
    for rim in RIMS:
        for sigma in blurs:
            for noise in noises:
                key = f"rim{int(rim)}/σ{sigma}/n{int(noise)}"
                res["one_d"][key] = {
                    k: dataclasses.asdict(sweep_1d(f, sigma, noise, rim,
                                                   seeds // SEED_DIVISOR.get(k, 1)))
                    for k, f in ESTIMATORS.items()}
    e2e_seeds = max(4, seeds // 12)
    for rim in RIMS[:2]:
        for sigma in blurs:
            for noise in (0.0, 5.0):
                key = f"rim{int(rim)}/σ{sigma}/n{int(noise)}"
                res["length"][key] = {
                    k: dataclasses.asdict(length_sweep(k, sigma, noise, rim, e2e_seeds,
                                                       pipeline=False)) for k in E2E}
    for sigma in (0.0, 0.8) if quick else (0.0, 0.8, 1.2):
        for noise in (0.0, 2.0):
            key = f"rim0/σ{sigma}/n{int(noise)}"
            res["pipeline"][key] = {
                f"{k}{'+linear' if lin else ''}": dataclasses.asdict(length_sweep(
                    k, sigma, noise, 0.0, 3, pipeline=True, linear=lin))
                for k in PIPELINE for lin in (False, True)}
    res["timing"] = timings()
    return res


def _table(block: dict, cols: Sequence[str], metric: str) -> List[str]:
    lines = ["| 조건 | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for key, row in block.items():
        cells = []
        for c in cols:
            st = row.get(c)
            if st is None:
                cells.append("—")
                continue
            cell = f"{st[metric]:.3f}"
            if st["fails"]:
                cell += f" ({st['fails']}✗)"
            cells.append(cell)
        lines.append(f"| {key} | " + " | ".join(cells) + " |")
    return lines


def to_markdown(res: dict) -> str:
    out = ["# 끝단 서브픽셀 추정기 벤치마크 — 자동 생성 표", "",
           f"위상 {res['phases']}점 × 상승·하강, 노이즈 조건 시드 {res['seeds']}"
           "(esf_fit 은 1/4). 단위 px. (n✗) = |오차|>1px(길이는 >2px) 또는 추정 실패 수.", ""]
    cols = list(ESTIMATORS)
    out += ["## 1D 최대 |치우침| — 양자화 없음, 노이즈 0 (추정기 자체의 S자)", ""]
    out += _table(res["one_d_float"], cols, "max_bias") + [""]
    for metric, title in (("max_bias", "1D 최대 |치우침| (8비트)"),
                          ("mean_bias", "1D 평균 |치우침| (8비트)"), ("std", "1D 노이즈 σ")):
        out += [f"## {title}", ""] + _table(res["one_d"], cols, metric) + [""]
    for metric, title in (("max_bias", "길이(measure_length_ex) 최대 |오차|"),
                          ("std", "길이(measure_length_ex) 노이즈 σ")):
        out += [f"## {title}", ""] + _table(res["length"], list(E2E), metric) + [""]
    pcols = [f"{k}{s}" for k in PIPELINE for s in ("", "+linear")]
    out += ["## InspectionPipeline 길이 최대 |오차| (+linear = 선형 그레이에서 위치)", ""]
    out += _table(res["pipeline"], pcols, "max_bias") + [""]
    out += ["## 시간", ""] + [f"- {k}: {v:.2f}" for k, v in res["timing"].items()] + [""]
    return "\n".join(out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="끝단 서브픽셀 추정기 벤치마크")
    ap.add_argument("--seeds", type=int, default=100, help="노이즈 조건 위상당 시드 수")
    ap.add_argument("--quick", action="store_true", help="조건을 줄여 빨리(점검용)")
    ap.add_argument("--md", type=Path, default=None, help="표 마크다운 저장 경로")
    ap.add_argument("--json", type=Path, default=None, help="원자료 JSON 저장 경로")
    a = ap.parse_args(argv)
    res = run(a.seeds, quick=a.quick)
    md = to_markdown(res)
    print(md)
    if a.md:
        a.md.write_text(md, encoding="utf-8")
    if a.json:
        a.json.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
