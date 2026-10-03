"""끝단 직선 적합 — 기울기 보정과 그 안전장치.

합성 이미지는 **회전으로 만들지 않는다.** cv2.warpAffine 은 보간으로 끝단을
번지게 해 길이 자체를 바꾸므로, 검증하려는 양을 오염시킨다. 대신 기운
평행사변형을 직접 그려 **수직거리를 정확히 length_px 로 유지**한다.
"""
from __future__ import annotations

import math

import cv2
import numpy as np
import pytest
from aivis_types import ItemMaster

from vision.length.edges import (
    MAX_TILT_DEG,
    MIN_ROWS_ABS,
    fit_edge_lines,
    tilt_overmeasure_mm,
)
from vision.length.measure import _find_edges, measure_length_ex

SCALE = 0.25          # mm/px
LENGTH_PX = 500.0     # → 125.000mm
TRUE_MM = LENGTH_PX * SCALE


@pytest.fixture
def item() -> ItemMaster:
    return ItemMaster(
        item_code="T", item_name="tilt", ref_length_mm=TRUE_MM,
        tol_plus_mm=5.0, tol_minus_mm=5.0, px_to_mm_scale=SCALE,
    )


def tilted_tube(
    tilt_deg: float, *, h: int = 400, w: int = 900,
    length_px: float = LENGTH_PX, od_px: int = 200,
    bg: int = 30, fg: int = 220,
) -> np.ndarray:
    """두 끝단 직선 사이의 수직거리가 정확히 length_px 인 기운 막대."""
    img = np.full((h, w), bg, np.uint8)
    b = math.tan(math.radians(tilt_deg))
    cx, cy = w / 2, h / 2
    half, norm = length_px / 2, math.sqrt(1 + b * b)
    pts = []
    for sgn in (-1, 1):
        a = cx + sgn * half * norm      # 수평 절편(수직거리를 보존하도록)
        for dy in (-od_px / 2, od_px / 2):
            pts.append((a + b * dy, cy + dy))
    poly = np.array([pts[0], pts[1], pts[3], pts[2]], np.int32)
    cv2.fillPoly(img, [poly], fg)
    return img


# --- 기하 자체 -------------------------------------------------------------

def test_overmeasure_formula():
    """세로평균의 수평거리는 참값의 1/cosθ 배다."""
    assert tilt_overmeasure_mm(250.0, 2.0) == pytest.approx(0.1524, abs=1e-3)
    assert tilt_overmeasure_mm(250.0, 0.0) == pytest.approx(0.0)
    # 공차 ±0.1mm(폭 0.2) 에서 2° 는 혼자 공차의 76% 를 먹는다.
    assert tilt_overmeasure_mm(250.0, 2.0) / 0.2 > 0.7


def test_fit_recovers_the_tilt_angle():
    for truth in (0.0, 1.0, 3.0, 6.0):
        roi = tilted_tube(truth)
        prof = roi.astype(np.float32).mean(axis=0)
        coarse = _find_edges(prof, 20.0)
        fit = fit_edge_lines(roi, *(coarse if coarse else (0.0, roi.shape[1] - 1.0)))
        assert fit is not None, f"{truth}° 에서 적합 실패"
        assert fit.tilt_deg == pytest.approx(truth, abs=0.25)


def test_perpendicular_is_shorter_than_horizontal_when_tilted():
    fit = fit_edge_lines(tilted_tube(5.0), 200.0, 700.0)
    assert fit is not None
    assert fit.perpendicular_px < fit.horizontal_px
    assert fit.perpendicular_px == pytest.approx(
        fit.horizontal_px * math.cos(math.radians(fit.tilt_deg)), rel=1e-9
    )


def test_end_faces_come_out_parallel():
    fit = fit_edge_lines(tilted_tube(4.0), 200.0, 700.0)
    assert fit is not None
    assert fit.parallelism_deg < 1.0


# --- 측정 경로에 미치는 효과 ------------------------------------------------

def test_correction_removes_the_tilt_bias(item):
    """보정을 켜면 기울어도 참값에 붙는다."""
    for tilt in (0.0, 1.0, 2.0):
        on, _ = measure_length_ex(tilted_tube(tilt), item, correct_tilt=True)
        assert on.meas_length_mm is not None
        assert abs(on.meas_length_mm - TRUE_MM) < 0.4, f"{tilt}°"


def test_uncorrected_path_breaks_down_past_a_few_degrees(item):
    """**기존 코드의 결함을 고정한다.**

    세로평균 프로파일은 기울기가 커지면 에지가 od_px·tanθ 만큼 희석돼,
    경계 아티팩트보다 약해지는 순간 끝단을 통째로 놓치고 ROI 폭을 길이로
    반환한다. 조용히 틀리는 것이 아니라 **크게** 틀린다.
    """
    roi = tilted_tube(5.0)
    off, _ = measure_length_ex(roi, item, correct_tilt=False)
    on, _ = measure_length_ex(roi, item, correct_tilt=True)
    assert off.meas_length_mm is not None and on.meas_length_mm is not None
    assert abs(off.meas_length_mm - TRUE_MM) > 50.0, "결함이 재현되지 않았다"
    assert abs(on.meas_length_mm - TRUE_MM) < 0.5


def test_correction_is_deterministic(item):
    """§5 M5 DoD: 동일 입력 → 동일 출력."""
    roi = tilted_tube(2.5)
    vals = {measure_length_ex(roi, item)[0].meas_length_mm for _ in range(5)}
    assert len(vals) == 1


def test_no_tilt_leaves_measurement_essentially_unchanged(item):
    """기울기가 없으면 보정이 값을 흔들지 않아야 한다(회귀 안전)."""
    roi = tilted_tube(0.0)
    off, _ = measure_length_ex(roi, item, correct_tilt=False)
    on, _ = measure_length_ex(roi, item, correct_tilt=True)
    assert abs(on.meas_length_mm - off.meas_length_mm) < 0.02


# --- 안전장치 (약한 적합이 측정을 덮어쓰지 못하게) --------------------------

def test_weak_fit_is_rejected_not_used():
    """행 몇 개짜리 적합의 기울기는 노이즈다 — 거부되어야 한다."""
    roi = tilted_tube(2.0, h=400, od_px=12)     # 12행짜리 얇은 막대
    fit = fit_edge_lines(roi, 200.0, 700.0)
    if fit is not None and min(fit.left.n_rows, fit.right.n_rows) < MIN_ROWS_ABS:
        assert fit.reject_reason() is not None


def test_implausible_tilt_is_rejected():
    roi = tilted_tube(2.0)
    fit = fit_edge_lines(roi, 200.0, 700.0)
    assert fit is not None and fit.reject_reason() is None
    # 물리적으로 불가능한 기울기는 오검출로 본다.
    from dataclasses import replace
    bogus = replace(fit, left=replace(fit.left, slope=math.tan(math.radians(45))),
                    right=replace(fit.right, slope=math.tan(math.radians(45))))
    assert bogus.reject_reason() is not None
    assert "기울기" in bogus.reject_reason()


def test_non_parallel_ends_are_rejected():
    from dataclasses import replace
    fit = fit_edge_lines(tilted_tube(1.0), 200.0, 700.0)
    assert fit is not None
    skewed = replace(fit, right=replace(fit.right, slope=fit.right.slope + 0.2))
    assert skewed.reject_reason() is not None


def test_row_count_mismatch_is_rejected():
    """한쪽만 많이 봤다면 한쪽은 끝단이 아닌 것을 보고 있다."""
    from dataclasses import replace
    fit = fit_edge_lines(tilted_tube(1.0), 200.0, 700.0)
    assert fit is not None
    lop = replace(fit, right=replace(fit.right, n_rows=max(MIN_ROWS_ABS, 9)))
    if fit.left.n_rows > 2 * lop.right.n_rows:
        assert lop.reject_reason() is not None


def test_tube_filling_the_roi_is_left_alone(item):
    """크롭이 튜브를 꽉 채워 끝단이 시야 밖이면 **손대지 않는다.**

    이때 창 안에서 찾은 피크는 끝단이 아니라 내부 구조라, 적합하면 측정
    구간을 안쪽으로 잘라먹어 길이를 깎는다(다발 경로에서 실제로 났던 일).
    """
    full = np.full((40, 500), 200, np.uint8)
    assert fit_edge_lines(full, 0.0, 499.0) is None
    on, _ = measure_length_ex(full, item, correct_tilt=True)
    off, _ = measure_length_ex(full, item, correct_tilt=False)
    assert on.meas_length_mm == off.meas_length_mm


def test_fit_survives_a_few_bad_rows():
    """MAD 이상치 제거: 반사로 몇 행이 튀어도 기울기가 끌려가면 안 된다."""
    roi = tilted_tube(2.0)
    roi[10:14, 100:140] = 255          # 엉뚱한 밝은 블록
    fit = fit_edge_lines(roi, 200.0, 700.0)
    assert fit is not None
    assert fit.tilt_deg == pytest.approx(2.0, abs=0.4)


def test_tilt_gate_constant_is_physically_motivated():
    """컨베이어 위 튜브가 10°를 넘을 수는 없다 — 상한이 느슨해지면 보호가 깨진다."""
    assert MAX_TILT_DEG <= 15.0
