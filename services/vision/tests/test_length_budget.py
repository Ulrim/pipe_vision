"""길이 오차 예산 — 공차 타당성 계산이 물리와 맞는지 검증.

여기서 지키려는 것은 "코드가 돈다"가 아니라 **숫자가 맞다**이다. 각 항을 손으로
계산할 수 있는 값으로 고정해 둔다. 식을 잘못 고치면 테스트가 깨져야 한다.
"""
from __future__ import annotations

import math

import pytest

from vision.quality.budget import (
    CTE_ALUMINIUM,
    OpticalSetup,
    fit_frame,
    length_budget,
    render_md,
    required_fov_mm,
)


def _only(**over) -> OpticalSetup:
    """한 항만 남기고 나머지를 0 으로 — 항별 검증용."""
    base = dict(
        length_mm=250.0, fov_mm=250.0, sensor_px=2500,   # 정확히 0.1 mm/px
        working_distance_mm=500.0,
        edge_sigma_px=0.0, distortion_residual_px=0.0,
        height_sigma_mm=0.0, temp_sigma_k=0.0, scale_rel_sigma=0.0,
    )
    base.update(over)
    return OpticalSetup(**base)


def test_mm_per_px():
    assert _only().mm_per_px == pytest.approx(0.1)


def test_edge_term_is_root_two_times_pixel_noise():
    """양 끝단을 각각 재므로 √2 배다 — 1배로 쓰면 예산을 과소평가한다."""
    r = length_budget(_only(edge_sigma_px=0.1), tol_plus_mm=0.1, tol_minus_mm=0.1)
    assert r.random_terms["끝단검출"] == pytest.approx(math.sqrt(2) * 0.1 * 0.1)


def test_height_term_scales_with_length_over_working_distance():
    """닮은꼴: 오차 = 길이 × Δz / 작업거리. 250×0.05/500 = 0.025mm."""
    r = length_budget(_only(height_sigma_mm=0.05), tol_plus_mm=0.1, tol_minus_mm=0.1)
    assert r.random_terms["높이반복성"] == pytest.approx(0.025)


def test_longer_working_distance_reduces_height_sensitivity():
    near = length_budget(_only(height_sigma_mm=0.05, working_distance_mm=500.0),
                         tol_plus_mm=0.1, tol_minus_mm=0.1)
    far = length_budget(_only(height_sigma_mm=0.05, working_distance_mm=1000.0),
                        tol_plus_mm=0.1, tol_minus_mm=0.1)
    assert far.random_terms["높이반복성"] == pytest.approx(
        near.random_terms["높이반복성"] / 2
    )


def test_thermal_term_matches_aluminium_cte():
    """250mm 알루미늄, 1K → 0.00578mm. 공차 0.2mm 폭의 17%(6σ)를 온도가 먹는다."""
    r = length_budget(_only(temp_sigma_k=1.0), tol_plus_mm=0.1, tol_minus_mm=0.1)
    assert r.random_terms["열팽창"] == pytest.approx(250.0 * CTE_ALUMINIUM * 1.0)
    assert r.pct_grr == pytest.approx(6 * 250 * CTE_ALUMINIUM / 0.2 * 100, rel=1e-9)


def test_tolerance_is_the_band_width_not_the_half():
    """±0.1 의 공차 '폭'은 0.2 다. 0.1 로 나누면 %GR&R 이 2배로 부풀어 오판한다."""
    r = length_budget(_only(), tol_plus_mm=0.1, tol_minus_mm=0.1)
    assert r.tolerance_mm == pytest.approx(0.2)


def test_terms_combine_in_quadrature_but_bias_adds_linearly():
    s = _only(edge_sigma_px=0.1, height_sigma_mm=0.05,
              height_offset_mm=0.1, temp_offset_k=10.0)
    r = length_budget(s, tol_plus_mm=0.1, tol_minus_mm=0.1)
    expect_sigma = math.hypot(math.sqrt(2) * 0.1 * 0.1, 0.025)
    assert r.sigma_mm == pytest.approx(expect_sigma)
    # 계통은 같은 방향으로 더해질 수 있으므로 단순합.
    assert r.bias_mm == pytest.approx(
        250 * 0.1 / 500 + 250 * CTE_ALUMINIUM * 10.0
    )


def test_bias_does_not_enter_grr():
    """품목별 보정계수가 흡수하는 양이므로 반복성 지표에 넣으면 안 된다."""
    clean = length_budget(_only(edge_sigma_px=0.1), tol_plus_mm=0.1, tol_minus_mm=0.1)
    biased = length_budget(_only(edge_sigma_px=0.1, height_offset_mm=1.0),
                           tol_plus_mm=0.1, tol_minus_mm=0.1)
    assert biased.pct_grr == pytest.approx(clean.pct_grr)
    assert biased.bias_mm > 0


def test_rolling_shutter_bias_vanishes_when_stationary():
    moving = length_budget(
        _only(conveyor_speed_mm_s=200.0, rolling_readout_s=1 / 14),
        tol_plus_mm=0.1, tol_minus_mm=0.1,
    )
    still = length_budget(
        _only(conveyor_speed_mm_s=0.0, rolling_readout_s=1 / 14),
        tol_plus_mm=0.1, tol_minus_mm=0.1,
    )
    # 250mm 제품이 시야 250mm 를 꽉 채우므로 시차 = 전체 읽기시간.
    assert moving.bias_terms["롤링셔터"] == pytest.approx(200.0 / 14)
    assert "롤링셔터" not in still.bias_terms


def test_verdict_thresholds():
    # σ 를 직접 만들기 위해 끝단검출 항만 쓴다.
    def pct(sigma_target: float):
        # √2·σ_px·mmpx = sigma_target, mmpx=0.1 → σ_px = target/(√2·0.1)
        s = _only(edge_sigma_px=sigma_target / (math.sqrt(2) * 0.1))
        return length_budget(s, tol_plus_mm=0.1, tol_minus_mm=0.1)

    assert pct(0.2 * 0.10 / 6).verdict == "GOOD"       # 정확히 10%
    assert pct(0.2 * 0.20 / 6).verdict == "MARGINAL"
    assert pct(0.2 * 0.30 / 6).verdict == "MARGINAL"   # 경계는 수용
    assert pct(0.2 * 0.31 / 6).verdict == "FAIL"


def test_single_overhead_pi_camera_cannot_hold_point_one_mm():
    """핵심 결론의 회귀 방지.

    낙관적으로 잡아도(HQ 센서, 넉넉한 작업거리, V홈, 온도 1K) 전장 1카메라로
    ±0.1mm 는 불가능하다. 이 테스트가 통과하는 쪽으로 바뀌었다면 가정이
    느슨해진 것이니 숫자를 다시 보라.
    """
    s = OpticalSetup(
        length_mm=250.0, fov_mm=287.0, sensor_px=4056,
        working_distance_mm=800.0,
        height_sigma_mm=0.02, temp_sigma_k=1.0,
    )
    r = length_budget(s, tol_plus_mm=0.1, tol_minus_mm=0.1)
    assert not r.passed
    assert r.pct_grr > 50.0


def test_fixturing_and_temperature_dominate_not_the_camera():
    """센서를 무한히 좋게 해도(분해능 항 0) 치구·온도만으로 이미 공차를 넘는다.

    이게 "더 좋은 카메라를 사면 되지 않나" 에 대한 답이다.
    """
    s = OpticalSetup(
        length_mm=250.0, fov_mm=287.0, sensor_px=4056,
        working_distance_mm=500.0,
        edge_sigma_px=0.0, distortion_residual_px=0.0,   # 완벽한 카메라
        height_sigma_mm=0.05, temp_sigma_k=3.0, scale_rel_sigma=0.0,
    )
    r = length_budget(s, tol_plus_mm=0.1, tol_minus_mm=0.1)
    assert not r.passed
    assert r.dominant(1)[0][0] == "높이반복성"


def test_fit_frame_picks_the_better_orientation():
    """긴 축을 어디에 둘지로 분해능이 갈린다."""
    wide = fit_frame(sensor_long_px=4608, sensor_short_px=2592,
                     bundle_width_mm=300.0, min_length_window_mm=40.0)
    assert wide.rotated, "폭이 넓으면 긴 축을 폭 방향으로 눕히는 쪽이 낫다"
    assert wide.binding == "width"
    assert wide.mm_per_px == pytest.approx(300.0 / 4608)


def test_fit_frame_length_bound_when_bundle_is_narrow():
    narrow = fit_frame(sensor_long_px=4608, sensor_short_px=2592,
                       bundle_width_mm=30.0, min_length_window_mm=287.0)
    assert narrow.binding == "length"
    assert not narrow.rotated


def test_bundle_width_destroys_the_two_camera_escape():
    """끝단만 좁게 보는 2카메라는 **단품**일 때만 통한다.

    다발을 같이 담아야 하면 폭이 분해능을 붙잡으므로 창을 좁혀도 소용이 없다.
    이 성질을 잃으면 설계 판단이 틀어진다.
    """
    single = fit_frame(sensor_long_px=4608, sensor_short_px=2592,
                       bundle_width_mm=20.0, min_length_window_mm=40.0)
    bundled = fit_frame(sensor_long_px=4608, sensor_short_px=2592,
                        bundle_width_mm=300.0, min_length_window_mm=40.0)
    assert bundled.mm_per_px > single.mm_per_px * 5


def test_required_fov_inverts_the_budget():
    fov = required_fov_mm(tol_plus_mm=0.1, tol_minus_mm=0.1,
                          sensor_px=4608, edge_sigma_px=0.1, share=0.5)
    s = OpticalSetup(
        length_mm=fov, fov_mm=fov, sensor_px=4608,
        working_distance_mm=1e9,            # 깊이 항 제거
        edge_sigma_px=0.1, distortion_residual_px=0.0,
        height_sigma_mm=0.0, temp_sigma_k=0.0, scale_rel_sigma=0.0,
    )
    r = length_budget(s, tol_plus_mm=0.1, tol_minus_mm=0.1)
    assert r.pct_grr == pytest.approx(15.0, rel=1e-6)   # 30% 의 절반


def test_render_md_reports_the_verdict():
    r = length_budget(_only(edge_sigma_px=0.1), tol_plus_mm=0.1, tol_minus_mm=0.1)
    md = render_md(r)
    assert "%GR&R" in md and r.verdict in md
    assert "끝단검출" in md
