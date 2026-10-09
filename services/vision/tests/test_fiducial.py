"""프레임별 기준자 스케일 + 온도 환산.

핵심 검증은 "숫자가 나온다"가 아니라 **배율이 흔들려도 길이가 안 흔들린다**이다.
저장된 상수 스케일로는 불가능한 일이고, 그게 이 모듈의 존재 이유다.
"""
from __future__ import annotations


import cv2
import numpy as np
import pytest

from vision.calib.fiducial import (
    CTE_ALUMINIUM,
    FiducialError,
    measure_scale,
    to_reference_temperature,
)

PITCH_MM = 10.0


def gauge_image(
    *, pitch_px: float, n_marks: int = 11, h: int = 60, x0: float = 40.0,
    jitter: np.ndarray | None = None, mark_r: int = 6,
) -> np.ndarray:
    """밝은 바탕에 어두운 원형 마크를 등간격으로."""
    w = int(x0 * 2 + pitch_px * (n_marks - 1))
    img = np.full((h, w), 230, np.uint8)
    for i in range(n_marks):
        cx = x0 + i * pitch_px + (0.0 if jitter is None else float(jitter[i]))
        cv2.circle(img, (int(round(cx)), h // 2), mark_r, 20, -1)
    return img


def test_reads_the_scale():
    s = measure_scale(gauge_image(pitch_px=20.0), pitch_mm=PITCH_MM)
    assert s.n_marks == 11
    assert s.mm_per_px == pytest.approx(PITCH_MM / 20.0, rel=2e-3)


def test_scale_tracks_magnification_change():
    """배율이 5% 변하면 mm/px 도 5% 변해야 한다 — 그래야 길이가 안 변한다."""
    base = measure_scale(gauge_image(pitch_px=20.0), pitch_mm=PITCH_MM)
    zoom = measure_scale(gauge_image(pitch_px=21.0), pitch_mm=PITCH_MM)
    assert zoom.mm_per_px == pytest.approx(base.mm_per_px / 1.05, rel=3e-3)


def test_height_change_cancels_when_scale_is_measured_per_frame():
    """이 모듈의 존재 이유를 직접 확인한다.

    제품 높이가 변해 배율이 1.2% 커졌다고 하자(250mm·WD500 에서 Δz 6mm 에
    해당). 저장된 상수 스케일을 쓰면 길이가 1.2% = **3mm** 틀어진다. 기준자를
    같은 평면에서 매 프레임 재면 기준자도 같이 커지므로 상쇄된다.
    """
    mag = 1.012
    true_mm = 250.0
    base_px_per_mm = 4.0

    # 상수 스케일: 보정 시점의 mm/px 을 계속 쓴다.
    stored_mm_per_px = 1.0 / base_px_per_mm
    measured_px = true_mm * base_px_per_mm * mag
    stored_result = measured_px * stored_mm_per_px
    assert abs(stored_result - true_mm) > 2.9          # 3mm 틀어진다

    # 프레임별 스케일: 기준자도 같은 배율을 받는다.
    s = measure_scale(
        gauge_image(pitch_px=base_px_per_mm * PITCH_MM * mag), pitch_mm=PITCH_MM
    )
    per_frame_result = measured_px * s.mm_per_px
    assert per_frame_result == pytest.approx(true_mm, rel=3e-3)


def test_more_marks_beat_two_marks_under_noise():
    """끝 두 점만 쓰면 그 두 점의 노이즈가 스케일에 그대로 들어간다."""
    rng = np.random.default_rng(7)
    errs_fit, errs_ends = [], []
    for _ in range(40):
        j = rng.normal(0.0, 0.8, 11)
        img = gauge_image(pitch_px=20.0, jitter=j)
        s = measure_scale(img, pitch_mm=PITCH_MM)
        errs_fit.append(abs(s.mm_per_px - PITCH_MM / 20.0))
        ends = (s.centers_px[-1] - s.centers_px[0]) / 10.0
        errs_ends.append(abs(PITCH_MM / ends - PITCH_MM / 20.0))
    assert np.mean(errs_fit) < np.mean(errs_ends)


def test_too_few_marks_fails_loudly():
    with pytest.raises(FiducialError, match="마크"):
        measure_scale(gauge_image(pitch_px=20.0, n_marks=2), pitch_mm=PITCH_MM)


def test_misdetected_marks_fail_rather_than_return_a_wrong_scale():
    """틀린 스케일을 조용히 쓰면 전수 오판이 난다. 차라리 실패가 낫다."""
    j = np.array([0, 0, 0, 25.0, 0, 0, -22.0, 0, 0, 0, 0])  # 등간격 심하게 깨짐
    with pytest.raises(FiducialError, match="잔차"):
        measure_scale(gauge_image(pitch_px=20.0, jitter=j), pitch_mm=PITCH_MM)


def test_roi_limits_where_marks_are_looked_for():
    """운영에서는 기준자 띠만 봐야 한다 — 제품을 마크로 집으면 안 된다."""
    gauge = gauge_image(pitch_px=20.0)
    h, w = gauge.shape
    frame = np.full((h * 3, w), 230, np.uint8)
    frame[0:h] = gauge
    cv2.rectangle(frame, (50, h + 20), (w - 50, h * 3 - 20), 20, -1)  # '제품'
    s = measure_scale(frame, pitch_mm=PITCH_MM, roi=(0, 0, w, h))
    assert s.n_marks == 11
    assert s.mm_per_px == pytest.approx(PITCH_MM / 20.0, rel=2e-3)


def test_roi_outside_image_fails():
    with pytest.raises(FiducialError):
        measure_scale(gauge_image(pitch_px=20.0), pitch_mm=PITCH_MM,
                      roi=(10_000, 10_000, 50, 50))


def test_zero_pitch_rejected():
    with pytest.raises(FiducialError):
        measure_scale(gauge_image(pitch_px=20.0), pitch_mm=0.0)


# --- 온도 환산 -------------------------------------------------------------

def test_aluminium_expands_as_physics_says():
    """250mm 알루미늄은 1K 에 5.8µm."""
    hot = 250.0 * (1 + CTE_ALUMINIUM * 10.0)      # 30°C 에서의 실제 길이
    back = to_reference_temperature(hot, 30.0)
    assert back == pytest.approx(250.0, abs=1e-6)
    assert hot - 250.0 == pytest.approx(0.0578, abs=1e-4)


def test_reference_temperature_is_a_noop_at_twenty():
    assert to_reference_temperature(250.0, 20.0) == pytest.approx(250.0)


def test_ten_kelvin_eats_a_third_of_a_point_one_tolerance():
    """±0.1mm(폭 0.2) 에서 10K 는 공차의 29% 다 — 온도를 모르면 이만큼 모른다."""
    drift = 250.0 * CTE_ALUMINIUM * 10.0
    assert drift / 0.2 == pytest.approx(0.289, abs=0.01)


def test_absurd_temperature_rejected():
    with pytest.raises(ValueError):
        to_reference_temperature(250.0, -1e9)


# --- 자 읽기(보간) vs 곱셈 --------------------------------------------------

def test_interpolation_matches_multiplication_on_an_ideal_gauge():
    """왜곡이 없으면 둘이 같아야 한다 — 보간이 평소에 해를 끼치지 않는지."""
    s = measure_scale(gauge_image(pitch_px=20.0), pitch_mm=PITCH_MM)
    for x in (60.0, 140.0, 220.0):
        assert s.position_to_mm(x) == pytest.approx(
            (x - s.centers_px[0]) * s.mm_per_px, abs=0.05
        )


def test_interpolation_absorbs_distortion_that_multiplication_cannot():
    """이 모듈이 왜 곱셈이 아니라 보간을 쓰는지를 직접 보인다.

    렌즈 잔차가 남아 화면 가장자리의 배율이 중앙보다 2% 작다고 하자. 기준자도
    같은 왜곡을 받으므로, 끝단 **바로 옆** 마크로 읽으면 그 자리의 배율이
    적용된다. 전체 평균 mm/px 를 곱하면 가장자리에서 그만큼 틀린다.
    """
    n, pitch = 21, 20.0
    centre = (n - 1) * pitch / 2

    def warp(x_rel: float) -> float:            # 가장자리를 눌러 보이게
        return x_rel * (1.0 - 0.02 * (x_rel / centre) ** 2)

    xs = [40.0 + centre + warp(i * pitch - centre) for i in range(n)]
    img = np.full((60, int(xs[-1] + 40)), 230, np.uint8)
    for cx in xs:
        cv2.circle(img, (int(round(cx)), 30), 6, 20, -1)
    s = measure_scale(img, pitch_mm=PITCH_MM, max_residual_px=5.0)

    # 가장자리 가까운 마크 두 개 사이의 참 거리는 정확히 pitch_mm 다.
    true_mm = PITCH_MM
    interp = s.span_mm(s.centers_px[0], s.centers_px[1])
    naive = (s.centers_px[1] - s.centers_px[0]) * s.mm_per_px
    assert interp == pytest.approx(true_mm, abs=0.02)
    assert abs(naive - true_mm) > abs(interp - true_mm) * 3


def test_span_is_symmetric_and_additive():
    s = measure_scale(gauge_image(pitch_px=20.0), pitch_mm=PITCH_MM)
    a, b, c = s.centers_px[1], s.centers_px[4], s.centers_px[9]
    assert s.span_mm(a, c) == pytest.approx(s.span_mm(a, b) + s.span_mm(b, c))
    assert s.span_mm(a, b) == pytest.approx(-s.span_mm(b, a))


def test_extrapolates_past_the_last_mark():
    """기준자가 제품보다 짧으면 외삽이 된다 — 죽지는 않되 이점은 사라진다."""
    s = measure_scale(gauge_image(pitch_px=20.0), pitch_mm=PITCH_MM)
    beyond = s.centers_px[-1] + 40.0
    assert s.position_to_mm(beyond) > s.position_to_mm(s.centers_px[-1])


# --- 측정 경로 통합 ---------------------------------------------------------

def test_measure_with_gauge_beats_stored_scale_when_magnification_drifts():
    """끝에서 끝까지의 통합 확인 — 배율이 흔들려도 길이가 버티는가.

    상수 스케일은 배율 1.2% 변화를 그대로 길이 오차로 내보낸다. 기준자를 자로
    읽으면 기준자도 같이 커지므로 읽은 값이 거의 변하지 않는다.
    """
    from aivis_types import ItemMaster

    from vision.length.measure import measure_length_ex

    PITCH_PX, OD, TRUE_MM = 20.0, 120, 100.0
    stored_scale = PITCH_MM / PITCH_PX          # 0.5 mm/px (보정 시점)

    def frame(mag: float):
        """위쪽에 기준자 띠, 아래쪽에 막대. 둘 다 같은 배율을 받는다."""
        pitch = PITCH_PX * mag
        n = 21
        gw = int(40 * 2 + pitch * (n - 1))
        img = np.full((40 + OD + 40, gw), 230, np.uint8)
        for i in range(n):
            cv2.circle(img, (int(round(40 + i * pitch)), 20), 6, 20, -1)
        # 막대: 참 길이 TRUE_MM → 화면에서 TRUE_MM/PITCH_MM * pitch 픽셀
        bar_px = TRUE_MM / PITCH_MM * pitch
        x0 = 40.0
        cv2.rectangle(img, (int(round(x0)), 50),
                      (int(round(x0 + bar_px)), 50 + OD), 20, -1)
        return img, (int(round(x0)) - 15, 50, int(round(bar_px)) + 30, OD)

    item = ItemMaster(item_code="G", item_name="g", ref_length_mm=TRUE_MM,
                      tol_plus_mm=5.0, tol_minus_mm=5.0,
                      px_to_mm_scale=stored_scale)

    for mag in (1.0, 1.012):
        img, (rx, ry, rw, rh) = frame(mag)
        roi = img[ry:ry + rh, rx:rx + rw]
        # 막대는 어두우므로 반전해 '밝은 제품' 가정에 맞춘다.
        roi = 255 - roi
        s = measure_scale(img, pitch_mm=PITCH_MM, roi=(0, 0, img.shape[1], 40))

        stored, _ = measure_length_ex(roi, item)
        ruled, _ = measure_length_ex(roi, item, gauge=s, roi_x_offset=rx)
        err_stored = abs(stored.meas_length_mm - TRUE_MM)
        err_ruled = abs(ruled.meas_length_mm - TRUE_MM)
        if mag == 1.0:
            assert err_ruled < 1.0 and err_stored < 1.0
        else:
            # 1.2% 배율 변화 → 상수 스케일은 100mm 에서 1.2mm 틀어진다.
            assert err_stored > 0.9, f"상수 스케일이 안 틀어졌다: {err_stored}"
            assert err_ruled < err_stored / 3, (
                f"자 읽기 {err_ruled:.3f} vs 상수 {err_stored:.3f}"
            )
