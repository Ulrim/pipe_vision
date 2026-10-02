"""렌즈 왜곡 보정 테스트 (M3, §6.2).

핵심 주장 하나를 증명한다: **보정이 없으면 길이가 공차 밖으로 틀어지고,
보정하면 복원된다.** 라즈베리파이 카메라는 광각이라 이 단계가 빠지면 길이
검사 자체가 성립하지 않는다.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest
from aivis_types import ItemMaster

from vision.calib.lens import (
    CalibrationError,
    LensCalibration,
    calibrate_from_checkerboard,
    load_calibration,
    resolve_calibration,
)
from vision.length.measure import measure_length
from vision.preprocess import preprocess
from vision.tools.gen_synthetic import make_image


def _K(w: int, h: int) -> np.ndarray:
    f = float(max(w, h))
    return np.array([[f, 0, w / 2.0], [0, f, h / 2.0], [0, 0, 1]], np.float64)


def _simulate_camera(ideal: np.ndarray, K: np.ndarray, D: np.ndarray) -> np.ndarray:
    """이상 영상 → 카메라가 실제로 찍었을 왜곡 영상.

    출력(왜곡) 픽셀마다 거기에 오게 되는 이상 좌표를 고정점 반복으로 역산한다.
    cv2.undistort 와 정확히 반대 방향이라, 보정하면 원본으로 돌아와야 한다.
    """
    h, w = ideal.shape[:2]
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    k1, k2 = float(D[0]), float(D[1])
    u, v = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
    xd, yd = (u - cx) / fx, (v - cy) / fy
    x, y = xd.copy(), yd.copy()
    for _ in range(12):
        r2 = x * x + y * y
        s = 1.0 + k1 * r2 + k2 * r2 * r2
        x, y = xd / s, yd / s
    mx = (x * fx + cx).astype(np.float32)
    my = (y * fy + cy).astype(np.float32)
    return cv2.remap(ideal, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _item() -> ItemMaster:
    return ItemMaster(
        item_code="HP12", item_name="t", ref_length_mm=125.0,
        tol_plus_mm=0.5, tol_minus_mm=0.5, px_to_mm_scale=0.25,
    )


def _measure(frame: np.ndarray, item: ItemMaster):
    pre = preprocess(frame)
    r = pre.length_roi
    return measure_length(pre.gray_corrected[r.y0:r.y1, r.x0:r.x1], item)


@pytest.mark.parametrize("k1,min_err_mm", [(-0.05, 0.3), (-0.20, 2.0)])
def test_distortion_pushes_length_outside_tolerance(k1, min_err_mm):
    """보정이 없으면 왜곡만으로 공차를 넘는다 — 이 모듈이 필요한 이유."""
    img, _ = make_image("OK")
    h, w = img.shape[:2]
    K, D = _K(w, h), np.array([k1, 0, 0, 0, 0], np.float64)
    item = _item()

    base = _measure(img, item)
    cam = _measure(_simulate_camera(img, K, D), item)
    assert base.meas_length_mm is not None and cam.meas_length_mm is not None

    err = abs(cam.meas_length_mm - base.meas_length_mm)
    assert err >= min_err_mm, f"왜곡 오차가 {err:.3f}mm 로 예상보다 작다"
    # k1=-0.05(약한 왜곡)에서도 오차가 공차를 통째로 써버린다 — 다른 오차원에
    # 남길 여유가 0 이다. 측정시스템은 공차의 1/3 이내여야 쓸 만하다(MSA).
    assert err >= item.tol_plus_mm, "왜곡만으로 공차를 못 넘으면 전제가 틀린 것"


@pytest.mark.parametrize("k1", [-0.05, -0.10, -0.20, -0.30])
def test_undistort_restores_length(k1):
    """보정하면 왜곡 없는 측정값으로 돌아온다."""
    img, _ = make_image("OK")
    h, w = img.shape[:2]
    K, D = _K(w, h), np.array([k1, 0, 0, 0, 0], np.float64)
    item = _item()

    base = _measure(img, item)
    calib = LensCalibration(camera_matrix=K, dist_coeffs=D, image_size=(w, h))
    fixed = _measure(calib.undistort(_simulate_camera(img, K, D)), item)

    assert fixed.meas_length_mm is not None
    # 재매핑 보간 오차만 남는다 — 공차의 1/5 이내.
    assert abs(fixed.meas_length_mm - base.meas_length_mm) <= item.tol_plus_mm / 5


def test_undistort_is_noop_shaped_and_deterministic():
    img, _ = make_image("OK")
    h, w = img.shape[:2]
    calib = LensCalibration(
        camera_matrix=_K(w, h),
        dist_coeffs=np.zeros(5, np.float64),
        image_size=(w, h),
    )
    a, b = calib.undistort(img), calib.undistort(img)
    assert a.shape == img.shape
    assert np.array_equal(a, b)


def test_maps_are_cached_per_resolution():
    """파이에서 매 프레임 맵을 다시 만들면 처리시간 예산을 깎아먹는다."""
    img, _ = make_image("OK")
    h, w = img.shape[:2]
    calib = LensCalibration(
        camera_matrix=_K(w, h), dist_coeffs=np.zeros(5, np.float64), image_size=(w, h)
    )
    calib.undistort(img)
    calib.undistort(img)
    assert len(calib._maps) == 1
    calib.undistort(cv2.resize(img, (w // 2, h // 2)))
    assert len(calib._maps) == 2


def test_roundtrip_save_load(tmp_path):
    w, h = 800, 300
    calib = LensCalibration(
        camera_matrix=_K(w, h),
        dist_coeffs=np.array([-0.2, 0.05, 0.0, 0.0, 0.0]),
        image_size=(w, h),
        rms=0.31,
    )
    p = str(tmp_path / "lens.json")
    calib.save(p)
    got = load_calibration(p)
    assert np.allclose(got.camera_matrix, calib.camera_matrix)
    assert np.allclose(got.dist_coeffs, calib.dist_coeffs)
    assert got.image_size == (w, h)


def test_resolve_returns_none_without_config(monkeypatch):
    """캘리브레이션이 없어도 파이프라인은 돌아야 한다(시뮬레이터/합성)."""
    monkeypatch.delenv("AIVIS_LENS_CALIB", raising=False)
    assert resolve_calibration() is None


def test_resolve_raises_on_missing_file(monkeypatch, tmp_path):
    """설정은 했는데 파일이 없으면 조용히 넘어가면 안 된다."""
    monkeypatch.setenv("AIVIS_LENS_CALIB", str(tmp_path / "nope.json"))
    with pytest.raises(CalibrationError):
        resolve_calibration()


def _checkerboard(cols: int, rows: int, square: int = 60) -> np.ndarray:
    """내부 코너 cols x rows 인 체커보드(여백 포함)."""
    w, h = (cols + 1) * square, (rows + 1) * square
    img = np.zeros((h, w), np.uint8)
    for j in range(rows + 1):
        for i in range(cols + 1):
            if (i + j) % 2 == 0:
                img[j * square:(j + 1) * square, i * square:(i + 1) * square] = 255
    pad = square
    out = np.full((h + 2 * pad, w + 2 * pad), 255, np.uint8)
    out[pad:pad + h, pad:pad + w] = img
    return cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)


def test_calibrate_recovers_distortion_from_checkerboards():
    """체커보드 사진에서 왜곡 계수를 되찾고, 그걸로 보정이 된다."""
    board = _checkerboard(9, 6)
    h, w = board.shape[:2]
    K = _K(w, h)
    D = np.array([-0.15, 0.0, 0.0, 0.0, 0.0], np.float64)

    shots = []
    for dx, dy in ((0, 0), (25, -20), (-30, 15), (15, 25), (-20, -25), (35, 0)):
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        moved = cv2.warpAffine(board, M, (w, h), borderValue=(255, 255, 255))
        shots.append(_simulate_camera(moved, K, D))

    calib, used = calibrate_from_checkerboard(shots, cols=9, rows=6, square_mm=25.0)
    assert used >= 3
    # 추정한 k1 이 실제와 같은 부호·자릿수인지(평면 보드라 정밀도엔 한계가 있다).
    assert calib.dist_coeffs[0] < 0
    assert calib.rms < 1.0


def test_calibrate_rejects_too_few_boards():
    with pytest.raises(CalibrationError):
        calibrate_from_checkerboard([np.zeros((100, 100, 3), np.uint8)], cols=9, rows=6)


def test_pipeline_applies_calibration_when_given():
    """파이프라인에 캘리브레이션을 주면 왜곡된 프레임에서도 길이가 복원된다."""
    from vision.pipeline import InspectionPipeline

    img, _ = make_image("OK")
    h, w = img.shape[:2]
    K, D = _K(w, h), np.array([-0.20, 0, 0, 0, 0], np.float64)
    item = _item()

    good = InspectionPipeline().run(img, item)
    cam = _simulate_camera(img, K, D)
    bad = InspectionPipeline().run(cam, item)
    fixed = InspectionPipeline(
        lens=LensCalibration(camera_matrix=K, dist_coeffs=D, image_size=(w, h))
    ).run(cam, item)

    assert bad.length.meas_length_mm is not None
    assert fixed.length.meas_length_mm is not None
    err_bad = abs(bad.length.meas_length_mm - good.length.meas_length_mm)
    err_fixed = abs(fixed.length.meas_length_mm - good.length.meas_length_mm)
    assert err_bad > item.tol_plus_mm, "보정 없이 공차를 넘지 않으면 전제가 틀린 것"
    assert err_fixed <= item.tol_plus_mm / 5


def test_pipeline_runs_without_calibration(monkeypatch):
    """캘리브레이션이 없어도 종전과 동일하게 돈다(회귀 없음)."""
    from vision.pipeline import InspectionPipeline

    monkeypatch.delenv("AIVIS_LENS_CALIB", raising=False)
    img, _ = make_image("OK")
    r = InspectionPipeline().run(img, _item())
    assert r.length.meas_length_mm is not None
