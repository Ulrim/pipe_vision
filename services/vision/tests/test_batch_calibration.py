"""다발(배치) 검사에서 렌즈 보정·기준자가 단일 경로와 같게 작동하는지.

다발은 상시 공정이다(부록 A.1). 그런데 inspect_batch 는 튜브 스트립마다 단일 파이프라인을
돌렸고, 그 안의 두 단계가 입력을 **전체 프레임**으로 가정했다.

1. 렌즈 보정 — 스트립을 찌그러진 센서 전체로 보고 스트립 중심 기준으로 방사 보정했다.
   끝단을 엉뚱하게 휘어 길이가 틀리거나 아예 못 잤다.
2. 기준자 — ROI(프레임 픽셀)를 스트립에 적용해 '기준자 읽기 실패'로 떨어졌다(NG+재확인).

이제 둘 다 전체 프레임에서 한 번 하고(InspectionPipeline.prepare_frame), 튜브 crop 에는
결과만 넘긴다. 여기서는 "같은 기하면 다발 결과 = 단일 결과" 를 직접 확인한다.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest
from aivis_types import ItemMaster

from vision.calib.fiducial import FiducialConfig, measure_scale
from vision.calib.lens import LensCalibration
from vision.multi import inspect_batch
from vision.pipeline import InspectionPipeline
from vision.preprocess.area import InspectArea
from vision.tests.test_lens_calib import _K, _simulate_camera
from vision.tools.gen_synthetic import BG_GRAY, make_multi_image

N_TUBES = 5
PITCH_MM = 10.0
RULER_H = 24
RULER_GAP = 20
K1 = -0.20


def _item(**kw) -> ItemMaster:
    base = dict(item_code="HP12", item_name="t", ref_length_mm=150.0,
                tol_plus_mm=1.0, tol_minus_mm=1.0, px_to_mm_scale=0.25)
    base.update(kw)
    return ItemMaster(**base)


def _bundle():
    """튜브가 화면 폭의 3/4 를 차지하는 다발 — 끝단이 왜곡이 큰 가장자리에 온다."""
    return make_multi_image(N_TUBES, w=800, tube_len_px=600, margin=40)


def _isolate(img: np.ndarray, box) -> np.ndarray:
    """같은 자리에 튜브 하나만 남긴 프레임(단일 경로 비교용)."""
    x0, y0, x1, y1 = box
    out = np.full_like(img, BG_GRAY)
    out[y0:y1, x0:x1] = img[y0:y1, x0:x1]
    return out


def _ruler_xs() -> list:
    """화면 전체를 덮는 눈금. 오른쪽 절반은 간격이 조금 넓다 — 끝단 옆 마크의
    국소 배율이 결과를 바꾸므로, 엉뚱한 좌표로 보간하면 드러난다."""
    xs, x = [], 20.0
    for i in range(20):
        xs.append(x)
        x += 40.0 if i < 9 else 40.6
    return xs


def _with_ruler(img: np.ndarray) -> tuple:
    """프레임 위에 기준자 띠를 붙인다. (프레임, 기준자 ROI(x,y,w,h), 튜브 행 오프셋)."""
    h, w = img.shape[:2]
    top = RULER_H + RULER_GAP
    out = np.full((h + top, w, 3), BG_GRAY, np.uint8)
    out[top:] = img
    out[:RULER_H] = 230
    for x in _ruler_xs():
        cv2.circle(out, (int(round(x)), RULER_H // 2), 6, (20, 20, 20), -1)
    return out, (0, 0, w, RULER_H), top


def _below_ruler(frame: np.ndarray) -> InspectArea:
    """기준자 띠를 뺀 검사 영역(현장에서 기준자는 제품이 아니다)."""
    h = frame.shape[0]
    return InspectArea(0.0, (RULER_H + RULER_GAP // 2) / h, 1.0, 1.0)


def _lens(w: int, h: int) -> LensCalibration:
    return LensCalibration(camera_matrix=_K(w, h),
                           dist_coeffs=np.array([K1, 0, 0, 0, 0], np.float64),
                           image_size=(w, h))


def _camera(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    return _simulate_camera(img, _K(w, h), np.array([K1, 0, 0, 0, 0], np.float64))


# --- 1. 렌즈 보정 -------------------------------------------------------------
@pytest.mark.parametrize("area", [None, InspectArea(0.05, 0.05, 0.95, 0.95)],
                         ids=["full", "area"])
def test_bundle_lengths_match_single_tube_with_lens_calibration(area):
    img, boxes = _bundle()
    h, w = img.shape[:2]
    item = _item()
    cam = _camera(img)

    raw = inspect_batch(cam, item, expected_count=N_TUBES, area=area)
    br = inspect_batch(cam, item, expected_count=N_TUBES, area=area,
                       pipeline=InspectionPipeline(lens=_lens(w, h)))
    assert br.count_detected == N_TUBES and br.calib_note is None

    for tube, box, before in zip(br.tubes, boxes, raw.tubes):
        single = InspectionPipeline(lens=_lens(w, h)).run(_camera(_isolate(img, box)), item)
        assert single.length.meas_length_mm is not None
        assert tube.length_mm is not None, f"튜브 {tube.index}: 끝단을 못 찾았다"
        assert tube.length_mm == pytest.approx(single.length.meas_length_mm, abs=0.05)
        assert not tube.review_flag
        # 전제: 보정 없이는 왜곡만으로 공차를 넘는다(이 테스트가 뭔가를 재고 있다).
        assert abs(before.length_mm - single.length.meas_length_mm) > item.tol_plus_mm


def test_bundle_undistorts_the_frame_once_not_per_strip(monkeypatch):
    img, _ = _bundle()
    h, w = img.shape[:2]
    lens = _lens(w, h)
    sizes = []
    orig = LensCalibration.undistort

    def spy(self, frame):
        sizes.append(frame.shape[:2])
        return orig(self, frame)

    monkeypatch.setattr(LensCalibration, "undistort", spy)
    inspect_batch(_camera(img), _item(), expected_count=N_TUBES,
                  pipeline=InspectionPipeline(lens=lens))
    assert sizes == [(h, w)], "튜브 스트립에 렌즈 보정을 다시 걸면 안 된다"


def test_lens_failure_makes_every_tube_ng_review():
    """단일 경로와 같다 — 보정이 실패한 프레임의 길이는 믿을 수 없다."""

    class Broken:
        def undistort(self, frame):
            raise ValueError("bad matrix")

    img, _ = _bundle()
    br = inspect_batch(img, _item(), expected_count=N_TUBES,
                       pipeline=InspectionPipeline(lens=Broken()))
    assert br.count_detected == N_TUBES
    assert "렌즈 보정 실패" in (br.calib_note or "")
    assert all(t.final_verdict == "NG" and t.review_flag for t in br.tubes)


# --- 2. 기준자 ---------------------------------------------------------------
@pytest.mark.parametrize("use_area", [False, True], ids=["full", "area"])
def test_bundle_uses_the_frame_ruler_without_fallback(use_area):
    img, boxes = _bundle()
    frame, roi, top = _with_ruler(img)
    area = _below_ruler(frame) if use_area else None
    cfg = FiducialConfig(pitch_mm=PITCH_MM, roi=roi)
    # 저장된 스케일은 일부러 틀리게 — 기준자를 안 쓰면 180mm 로 잰다. 기준자로 읽으면
    # 오른쪽 절반 눈금이 넓어 약 148.5mm 다(공차 ±3 안).
    item = _item(px_to_mm_scale=0.30, tol_plus_mm=3.0, tol_minus_mm=3.0)

    br = inspect_batch(frame, item, expected_count=N_TUBES, area=area,
                       pipeline=InspectionPipeline(fiducial=cfg))
    assert br.count_detected == N_TUBES
    assert br.calib_note is None, br.calib_note

    gauge = measure_scale(frame, pitch_mm=PITCH_MM, roi=roi)
    for tube, box in zip(br.tubes, boxes):
        assert tube.length_mm is not None
        assert not tube.review_flag and tube.final_verdict == "OK", "기준자 읽기 실패 기록이 남았다"
        assert abs(tube.length_mm - 150.0) < 3.0, "저장된 스케일(0.30)로 쟀다"
        # 국소 배율 보간은 **프레임 x** 에서: 끝단 바로 옆 마크로 읽은 값과 같아야 한다.
        span = tube.length_span
        assert span is not None
        assert tube.length_mm == pytest.approx(gauge.span_mm(span.left_x, span.right_x), abs=0.01)
        # 같은 기하의 단일 경로와 같은 값.
        x0, y0, x1, y1 = box
        iso = frame.copy()
        iso[top:] = _isolate(img, box)
        single = InspectionPipeline(fiducial=cfg).run(iso, item)
        assert tube.length_mm == pytest.approx(single.length.meas_length_mm, abs=0.02)


def test_bundle_reads_the_ruler_once_per_frame(monkeypatch):
    import vision.pipeline as pipeline_mod

    img, _ = _bundle()
    frame, roi, _top = _with_ruler(img)
    calls = []
    orig = pipeline_mod.measure_scale

    def spy(image, **kw):
        calls.append(image.shape[:2])
        return orig(image, **kw)

    monkeypatch.setattr(pipeline_mod, "measure_scale", spy)
    inspect_batch(frame, _item(), expected_count=N_TUBES, area=_below_ruler(frame),
                  pipeline=InspectionPipeline(fiducial=FiducialConfig(PITCH_MM, roi)))
    assert calls == [frame.shape[:2]], "기준자는 전체 프레임에서 한 번만 읽는다"

    # 길이를 보지 않는 모드면 읽지도 않는다(단일 경로와 같다).
    calls.clear()
    inspect_batch(frame, _item(), expected_count=N_TUBES, area=_below_ruler(frame),
                  stage="POST_WASH_SURFACE",
                  pipeline=InspectionPipeline(fiducial=FiducialConfig(PITCH_MM, roi)))
    assert calls == []


def test_unreadable_ruler_falls_back_once_and_is_recorded():
    img, _ = _bundle()
    frame, _roi, _top = _with_ruler(img)
    blank = (0, RULER_H + 2, 60, 10)          # 마크가 없는 배경
    br = inspect_batch(frame, _item(), expected_count=N_TUBES, area=_below_ruler(frame),
                       pipeline=InspectionPipeline(fiducial=FiducialConfig(PITCH_MM, blank)))
    assert "기준자" in (br.calib_note or "")
    # 저장된 스케일(0.25)로 재고, 단일 경로처럼 재확인으로 보낸다.
    assert all(t.length_mm == pytest.approx(149.75, abs=0.3) for t in br.tubes)
    assert all(t.review_flag for t in br.tubes)


# --- 3. 세로 다발 · 렌즈+기준자 함께 ----------------------------------------------
def _rot_roi(roi, frame_h: int):
    """프레임을 90° 시계방향으로 돌렸을 때의 (x, y, w, h)."""
    x, y, w, h = roi
    return (frame_h - (y + h), x, h, w)


def test_vertical_bundle_reads_a_vertical_ruler():
    """세로 다발은 crop 을 90° 돌려 잰다 — 기준자도 세로로 놓이고 y 로 읽는다."""
    img, _ = _bundle()
    frame, roi, _top = _with_ruler(img)
    item = _item(px_to_mm_scale=0.30)
    flat = inspect_batch(frame, item, expected_count=N_TUBES, area=_below_ruler(frame),
                         pipeline=InspectionPipeline(fiducial=FiducialConfig(PITCH_MM, roi)))

    v_frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    v_roi = _rot_roi(roi, frame.shape[0])
    a = _below_ruler(frame)
    v_area = InspectArea(1.0 - a.y1, a.x0, 1.0 - a.y0, a.x1)
    vert = inspect_batch(v_frame, item, axis="vertical", expected_count=N_TUBES, area=v_area,
                         pipeline=InspectionPipeline(fiducial=FiducialConfig(PITCH_MM, v_roi)))
    assert vert.calib_note is None, vert.calib_note
    assert vert.count_detected == N_TUBES
    # 회전하면 튜브 순서가 뒤집힌다(위→아래 가 오른쪽→왼쪽).
    got = sorted(t.length_mm for t in vert.tubes)
    want = sorted(t.length_mm for t in flat.tubes)
    assert got == pytest.approx(want, abs=0.02)
    assert all(not t.review_flag for t in vert.tubes)


def test_lens_and_ruler_together_match_single_tube():
    """현장 길이 스테이션 구성(AIVIS_LENS_CALIB + AIVIS_FIDUCIAL) 그대로."""
    img, boxes = _bundle()
    frame, roi, top = _with_ruler(img)
    h, w = frame.shape[:2]
    cfg = FiducialConfig(pitch_mm=PITCH_MM, roi=roi)
    item = _item(px_to_mm_scale=0.30)
    area = _below_ruler(frame)

    br = inspect_batch(_camera(frame), item, expected_count=N_TUBES, area=area,
                       pipeline=InspectionPipeline(lens=_lens(w, h), fiducial=cfg))
    assert br.calib_note is None, br.calib_note
    for tube, box in zip(br.tubes, boxes):
        iso = frame.copy()
        iso[top:] = _isolate(img, box)
        single = InspectionPipeline(lens=_lens(w, h), fiducial=cfg).run(
            _camera(iso), item)
        assert single.length.meas_length_mm is not None and not single.review_flag
        assert tube.length_mm == pytest.approx(single.length.meas_length_mm, abs=0.05)
        assert not tube.review_flag
