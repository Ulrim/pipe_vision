"""끝단 서브픽셀 추정기(vision.length.subpixel) — 2026-10-20.

종전 행별 경로의 기울기 3점 포물선은 끝단이 화소 안 어디에 걸리느냐(위상)에 따라 블러 σ
0.5~2px 에서 0.03~0.05px 치우쳤다(S자). 반복 측정으로는 안 보이는 치우침이라 정답을 아는
합성(해석적 렌더 — vision.tools.subpixel_benchmark)으로 고정한다. 전체 비교는
`models/benchmarks/SUBPIXEL_RESULT.md`.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pytest
from aivis_types import ItemMaster

import vision.pipeline as pipeline_mod
from vision.length.measure import _find_edges, _parabolic_subpixel, _smooth_gradient
from vision.length.measure import measure_length_ex
from vision.length.subpixel import CONTEXT_PX, edge_subpixel, edge_subpixel_rows
from vision.tools.subpixel_benchmark import edge_signal, est_parabola_raw, tube_frame

PHASES = np.round(np.arange(0.0, 1.0 + 1e-9, 0.05), 2)


def _q(v: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(v), 0, 255)


def _peak(s: np.ndarray, rising: bool) -> int:
    g = np.gradient(s) * (1.0 if rising else -1.0)
    return 10 + int(np.argmax(g[10:31]))


def _bias(est, sigma: float, *, rim: float = 0.0, quantize: bool = True) -> np.ndarray:
    """위상 21점 × 상승·하강의 (추정 − 참값)."""
    out = []
    for rising in (True, False):
        for ph in PHASES:
            s = edge_signal(20.0 + ph, sigma, rising=rising, rim=rim)
            s = _q(s) if quantize else s
            out.append(est(s, _peak(s, rising)) - (20.0 + ph))
    return np.array(out)


# --- 치우침(S자) ---------------------------------------------------------------
def test_sharp_area_sampled_step_is_exact():
    """끝단이 화소 하나에 걸친 계단의 중앙차분은 [f/2, 1/2, (1-f)/2] — 무게중심이 끝단 그 자체."""
    assert np.max(np.abs(_bias(edge_subpixel, 0.0, quantize=False))) < 1e-9


@pytest.mark.parametrize("sigma", [0.5, 0.8, 1.2, 2.0])
def test_blurred_step_bias_is_negligible_without_quantisation(sigma):
    """추정기 자체의 S자: 대칭 창을 끝단에 맞춰 옮기므로 블러가 넓어도 ≤0.003px
    (측정 0.00001/0.0010/0.0025/0.0018 — 종전 행별 포물선 0.039/0.038/0.025/0.011)."""
    assert np.max(np.abs(_bias(edge_subpixel, sigma, quantize=False))) <= 0.003


_BOUND_8BIT = {0.0: 0.005, 0.5: 0.007, 0.8: 0.010, 1.2: 0.012, 2.0: 0.025}


@pytest.mark.parametrize("sigma", [0.0, 0.5, 0.8, 1.2, 2.0])
def test_phase_sweep_bias_bound_8bit(sigma):
    """8비트 양자화까지 포함한 위상 스윕. 남는 것은 대부분 양자화(실제 노이즈가 흩뜨린다).

    측정: σ 0/0.5/0.8/1.2/2.0 → 0.0032/0.0048/0.0075/0.0097/0.0197px(결정적 — 한계는 그 바로 위).
    종전 행별 포물선: 0.003/0.045/0.043/0.031/0.050px — 블러가 있으면 절반 아래로 줄어야 한다.
    """
    new = np.max(np.abs(_bias(edge_subpixel, sigma)))
    assert new <= _BOUND_8BIT[sigma], f"σ={sigma}: {new:.4f}px"
    if sigma > 0:
        old = np.max(np.abs(_bias(est_parabola_raw, sigma)))
        assert new <= 0.5 * old, f"σ={sigma}: 새 {new:.4f} vs 종전 {old:.4f}"


# --- 대칭·견고성 -------------------------------------------------------------------
def test_rising_and_falling_are_mirror_symmetric():
    rng = np.random.default_rng(7)
    for ph in PHASES:
        s = _q(edge_signal(20.0 + ph, 0.8, rising=True) + rng.normal(0.0, 3.0, 41))
        i = _peak(s, True)
        a = edge_subpixel(s, i)
        b = edge_subpixel(s[::-1].copy(), s.size - 1 - i)
        assert a is not None and b is not None
        assert a == pytest.approx(s.size - 1 - b, abs=1e-9)


def test_integer_peak_off_by_one_gives_the_same_answer():
    """정수 끝단을 다른 영상(반사 보정본)에서 찾아 ±1 어긋나도 위치는 같다."""
    for sigma in (0.0, 0.8, 2.0):
        for ph in PHASES:
            s = _q(edge_signal(20.0 + ph, sigma, rising=True))
            i = _peak(s, True)
            ref = edge_subpixel(s, i)
            for d in (-1, 1):
                assert edge_subpixel(s, i + d) == pytest.approx(ref, abs=1e-9)


@pytest.mark.parametrize("sigma", [0.8, 1.2, 2.0])
def test_noise_is_lower_than_the_old_parabola_and_never_fails(sigma):
    """미분 3점이 아니라 창 전체를 쓰므로 노이즈에 덜 흔들리고, 극값이 흔들려도 1px 넘게 안 틀린다.

    벤치마크(노이즈 5 계조): σ 0.8/1.2/2.0 → 0.084/0.099/0.168px, 종전 0.099/0.196/0.464px
    (종전은 σ2 에서 |오차|>1px 316/4200 건).
    """
    rng = np.random.default_rng(int(sigma * 10))
    new, old = [], []
    for ph in PHASES:
        base = edge_signal(20.0 + ph, sigma, rising=True)
        for _ in range(40):
            s = _q(base + rng.normal(0.0, 5.0, base.size))
            i = _peak(s, True)
            x = edge_subpixel(s, i)
            assert x is not None and abs(x - 20.0 - ph) < 1.0
            new.append(x - 20.0 - ph)
            old.append(est_parabola_raw(s, i) - 20.0 - ph)
    assert abs(np.mean(new)) <= 0.015
    # 블러가 넓을수록 차이가 크다(종전은 평평한 기울기 정점에서 극값이 흔들린다).
    ratio = {0.8: 0.95, 1.2: 0.65, 2.0: 0.4}[sigma]
    assert np.std(new) <= ratio * np.std(old), f"새 {np.std(new):.3f} vs 종전 {np.std(old):.3f}"


def test_one_pixel_glint_on_the_end_is_ignored():
    """끝단 안쪽 첫 화소만 튀는 반짝임(gen_synthetic 의 끝단 선) — 3점 중앙값이 지운다."""
    s = np.full(41, 30.0)
    s[20:] = 185.0
    s[20] = 210.0                      # 끝단 = 19.5(화소 19|20 경계), 첫 화소만 밝다
    assert edge_subpixel(s, 20) == pytest.approx(19.5, abs=1e-9)
    assert edge_subpixel(s[::-1].copy(), 20) == pytest.approx(20.5, abs=1e-9)


@pytest.mark.parametrize("sigma", [0.0, 0.5, 0.8, 1.2, 2.0])
def test_bright_rim_bias_is_bounded(sigma):
    """화소 사이에 걸린 1px 테두리 반짝임(+25, 대비의 16%)은 어떤 추정기든 바깥으로 끈다
    (블러가 넓으면 반짝임이 끝단 질량의 일부라 구분할 정보 자체가 없다). 여기서는 그 크기가
    0.18px 안이고(측정 ≤0.169, 종전 포물선 ≤0.200) 1px 넘게 틀리지 않음을 고정한다."""
    b = _bias(edge_subpixel, sigma, rim=25.0)
    assert np.max(np.abs(b)) <= 0.18


# --- 인터페이스 --------------------------------------------------------------------
def test_rows_and_1d_agree():
    rng = np.random.default_rng(3)
    rows = np.stack([_q(edge_signal(20.0 + ph, 1.2, rising=True) + rng.normal(0.0, 2.0, 41))
                     for ph in PHASES])
    idx = np.array([_peak(r, True) for r in rows])
    xs = edge_subpixel_rows(rows, idx)
    for r, i, x in zip(rows, idx, xs):
        assert edge_subpixel(r, int(i)) == pytest.approx(x, abs=1e-12)


def test_degenerate_inputs():
    assert edge_subpixel(np.full(30, 80.0), 15) is None          # 평탄 — 방향이 없다
    assert edge_subpixel(np.arange(30.0), 30) is None             # 창 밖
    assert edge_subpixel(np.arange(2.0), 0) is None               # 너무 짧다
    with pytest.raises(ValueError):
        edge_subpixel_rows(np.zeros((2, 10)), np.array([3]))
    with pytest.raises(ValueError):
        edge_subpixel_rows(np.zeros((1, 10)), np.array([10]))
    # 창 가장자리 끝단: 바깥은 끝값으로 보고 잰다(실제 밝기를 주려면 CONTEXT_PX 만큼 넘긴다).
    s = _q(edge_signal(2.3, 0.8, rising=True))
    assert edge_subpixel(s, 2) == pytest.approx(2.3, abs=0.05)
    assert CONTEXT_PX >= 6


def test_find_edges_falls_back_to_parabola_when_position_signal_is_flat():
    """가드(보정본)는 통과했는데 위치 영상에 끝단이 없으면 종전 포물선으로 잰다 — 멈추지 않는다."""
    prof = _q(edge_signal(20.3, 0.8, rising=True))
    prof[30:] = 30.0                                              # 하강 끝단도 만든다
    flat = np.full_like(prof, 100.0)
    got = _find_edges(prof, 20.0, linear=flat)
    _sm, g = _smooth_gradient(prof)
    want = (_parabolic_subpixel(np.abs(g), int(np.argmax(g))),
            _parabolic_subpixel(np.abs(g), int(np.argmin(g))))
    assert got == pytest.approx(want)
    with pytest.raises(ValueError):
        _find_edges(prof, 20.0, linear=prof[:-1])


def test_measure_length_ex_rejects_mismatched_linear_window():
    item = ItemMaster(item_code="T", item_name="t", ref_length_mm=100.0, tol_plus_mm=1.0,
                      tol_minus_mm=1.0, px_to_mm_scale=1.0)
    win = np.full((20, 140), 30, np.uint8)
    win[:, 20:120] = 200
    with pytest.raises(ValueError):
        measure_length_ex(win, item, linear=win[:, :-1])


# --- 파이프라인: 위치는 선형 그레이에서 ------------------------------------------------
def _pipeline_errors(sigma: float) -> np.ndarray:
    pipe = pipeline_mod.InspectionPipeline()
    rng = np.random.default_rng(0)
    errs = []
    for j, ph in enumerate(PHASES[::2]):
        left, length = 200.0 + (0.37 * j) % 1.0, 500.0 + float(ph)
        item = ItemMaster(item_code="T", item_name="t", ref_length_mm=length, tol_plus_mm=5.0,
                          tol_minus_mm=5.0, px_to_mm_scale=1.0, expected_count=1)
        meas = pipe.run(tube_frame(left, length, sigma, 0.0, 0.0, rng), item).length.meas_length_mm
        errs.append(meas - length)
    return np.abs(np.array(errs))


@pytest.mark.parametrize("sigma", [0.0, 0.8])
def test_pipeline_locates_ends_on_linear_gray(sigma, monkeypatch):
    """반사 보정본(조명 나눗셈·CLAHE·미디언)은 끝단 걸침 화소의 밝기를 바꿔 화소 사이 위치를
    지운다 — 거기서 재면 추정기를 바꿔도 0.2px 넘게 남는다. 선형 그레이에서 재면 사라진다."""
    new = _pipeline_errors(sigma)
    assert new.max() <= 0.03, new
    real = pipeline_mod.preprocess
    monkeypatch.setattr(pipeline_mod, "preprocess",
                        lambda *a, **k: dataclasses.replace(real(*a, **k), gray=None))
    corrected_only = _pipeline_errors(sigma)
    assert corrected_only.max() >= 0.15, corrected_only
