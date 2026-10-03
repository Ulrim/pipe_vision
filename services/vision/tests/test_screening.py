"""선별 한계 계산 — 적분이 맞는지, 극단에서 상식과 맞는지.

여기서 지키려는 것은 "코드가 돈다"가 아니라 **확률이 맞다**이다. 이 숫자로
현장 선별 한계를 정하므로, 틀리면 멀쩡한 제품을 버리거나 불량을 내보낸다.
해석적으로 답을 아는 경우를 골라 고정한다.
"""
from __future__ import annotations

import math
from statistics import NormalDist

import pytest

from vision.quality.screening import (
    guard_band_for,
    screen_rates,
    sweep,
)

N = NormalDist()


def test_perfect_measurement_never_misjudges():
    """측정오차가 0 이면 오검·미검이 0 이어야 한다 — 적분의 기본 점검."""
    r = screen_rates(spec_mm=0.1, meas_sigma_mm=0.0, process_sigma_mm=0.05)
    assert r.false_reject == pytest.approx(0.0, abs=1e-9)
    assert r.false_accept == pytest.approx(0.0, abs=1e-9)


def test_true_good_matches_the_normal_cdf():
    """진짜 양품 비율은 측정과 무관하게 공정 분포만으로 정해진다."""
    sp, spec = 0.05, 0.1
    r = screen_rates(spec_mm=spec, meas_sigma_mm=0.03, process_sigma_mm=sp)
    expect = N.cdf(spec / sp) - N.cdf(-spec / sp)
    assert r.true_good == pytest.approx(expect, rel=1e-6)


def test_zero_process_spread_only_false_rejects():
    """모든 제품이 공칭값이면 불량이 없으므로 미검도 0, 오검만 남는다."""
    sm, spec = 0.0263, 0.1
    r = screen_rates(spec_mm=spec, meas_sigma_mm=sm, process_sigma_mm=0.0)
    assert r.true_good == 1.0
    assert r.false_accept == 0.0
    expect = 1.0 - (N.cdf(spec / sm) - N.cdf(-spec / sm))
    assert r.false_reject == pytest.approx(expect, rel=1e-9)


def test_worse_measurement_means_more_misjudgement():
    base = screen_rates(spec_mm=0.1, meas_sigma_mm=0.005, process_sigma_mm=0.03)
    worse = screen_rates(spec_mm=0.1, meas_sigma_mm=0.030, process_sigma_mm=0.03)
    assert worse.misjudge > base.misjudge


def test_tighter_process_means_less_misjudgement():
    tight = screen_rates(spec_mm=0.1, meas_sigma_mm=0.0263, process_sigma_mm=0.02)
    loose = screen_rates(spec_mm=0.1, meas_sigma_mm=0.0263, process_sigma_mm=0.08)
    assert tight.misjudge < loose.misjudge


# --- 가드밴드 -------------------------------------------------------------

def test_guard_band_trades_false_accept_for_false_reject():
    """조이면 미검은 줄고 오검은 는다 — 이 교환이 가드밴드의 전부다."""
    kw = dict(spec_mm=0.1, meas_sigma_mm=0.0263, process_sigma_mm=0.05)
    wide = screen_rates(**kw, screen_mm=0.1)
    tight = screen_rates(**kw, screen_mm=0.06)
    assert tight.false_accept < wide.false_accept
    assert tight.false_reject > wide.false_reject


def test_guard_band_for_meets_its_target():
    r = guard_band_for(spec_mm=0.1, meas_sigma_mm=0.0263,
                       process_sigma_mm=0.05, max_false_accept=0.001)
    assert r is not None
    assert r.false_accept <= 0.001
    assert r.screen_mm <= 0.1


def test_guard_band_picks_the_loosest_that_works():
    """조건을 만족하는 것 중 **가장 헐거운** 값이어야 한다.
    더 조이면 오검만 늘어 손해다."""
    kw = dict(spec_mm=0.1, meas_sigma_mm=0.0263, process_sigma_mm=0.05)
    r = guard_band_for(**kw, max_false_accept=0.001, steps=60)
    assert r is not None
    looser = screen_rates(**kw, screen_mm=min(0.1, r.screen_mm + 0.1 / 60))
    assert looser.false_accept > 0.001, "더 헐거운 값도 조건을 만족하면 안 된다"


def test_impossible_target_returns_none():
    """측정이 너무 거칠면 어떤 한계로도 보장할 수 없다 — 그렇다고 말해야 한다."""
    assert guard_band_for(spec_mm=0.1, meas_sigma_mm=0.5,
                          process_sigma_mm=0.2, max_false_accept=1e-9) is None


# --- 현장 사례 고정 -------------------------------------------------------

def test_the_430mm_case_with_camera_module_3():
    """현장 구성(제품 430mm, CM3, 측정 1σ=26.3µm)에서 ±0.1mm 선별.

    %GR&R 은 79% 로 AIAG 불합격이지만, 공정이 좁으면 실제 오판율은 작다.
    **두 지표가 다른 질문에 답한다**는 것을 숫자로 고정해 둔다.
    """
    sm = 0.0263
    tight = screen_rates(spec_mm=0.1, meas_sigma_mm=sm, process_sigma_mm=0.02)
    assert tight.misjudge < 0.01, "공정 1σ=20µm 면 오판 1% 미만"

    loose = screen_rates(spec_mm=0.1, meas_sigma_mm=sm, process_sigma_mm=0.08)
    assert loose.misjudge > 0.05, "공정 1σ=80µm 면 오판이 급증"


def test_sweep_returns_one_result_per_sigma():
    rs = sweep(spec_mm=0.1, meas_sigma_mm=0.0263,
               process_sigmas=[0.02, 0.03, 0.05])
    assert len(rs) == 3
    assert [r.process_sigma_mm for r in rs] == [0.02, 0.03, 0.05]
    # 산포가 커질수록 진짜 양품이 줄어든다.
    assert rs[0].true_good > rs[1].true_good > rs[2].true_good


def test_shipped_defect_ppm_counts_only_what_passes():
    """고객이 겪는 품질은 '통과분 중 불량' 이다. 전체 대비가 아니다."""
    r = screen_rates(spec_mm=0.1, meas_sigma_mm=0.0263, process_sigma_mm=0.08)
    passed = (r.true_good - r.false_reject) + r.false_accept
    assert r.shipped_defect_ppm == pytest.approx(
        r.false_accept / passed * 1e6, rel=1e-9
    )
    assert r.shipped_defect_ppm > r.false_accept * 1e6 * 0.9


def test_rejects_bad_input():
    with pytest.raises(ValueError):
        screen_rates(spec_mm=0.0, meas_sigma_mm=0.01, process_sigma_mm=0.01)
    with pytest.raises(ValueError):
        screen_rates(spec_mm=0.1, meas_sigma_mm=-1, process_sigma_mm=0.01)


def test_integration_is_deterministic():
    """몬테카를로가 아니다 — 같은 입력은 같은 숫자를 준다(리포트 재현성)."""
    kw = dict(spec_mm=0.1, meas_sigma_mm=0.0263, process_sigma_mm=0.05)
    a, b = screen_rates(**kw), screen_rates(**kw)
    assert a.false_reject == b.false_reject
    assert a.false_accept == b.false_accept


def test_integration_accuracy_against_a_known_case():
    """측정오차가 아주 작으면 오검은 'spec 경계 근처 폭' 으로 수렴한다.
    적분 정확도를 독립적으로 확인한다."""
    sp, spec, sm = 0.05, 0.1, 1e-6
    r = screen_rates(spec_mm=spec, meas_sigma_mm=sm, process_sigma_mm=sp)
    assert r.false_reject < 1e-4
    assert r.false_accept < 1e-4
    # 진짜 양품 비율은 정확해야 한다.
    assert r.true_good == pytest.approx(
        N.cdf(spec / sp) - N.cdf(-spec / sp), rel=1e-6
    )
