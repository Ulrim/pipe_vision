"""다발(배치) 길이 측정의 보정 경로 — 2026-10-17.

다발 검사는 튜브마다 crop 을 잘라 단일 파이프라인으로 잰다. 세 가지가 crop 에서 틀어졌다.

1. **렌즈 왜곡 보정**을 crop 마다 했다 — crop 을 센서 전체로 보고 엉뚱하게 휜다.
   → 전체 프레임에서 한 번 보정하고 그 프레임에서 나누고 자른다(prepare_frame).
2. **기준자**를 crop 에서 찾았다 — 프레임 좌표 ROI 가 crop 밖이라 '읽기 실패' → 튜브 전부
   NG + 재확인. → 전체 프레임에서 한 번 읽고, 튜브마다 기준자 좌표 원점·방향을 넘긴다.
   세로 다발은 기준자도 세로(프레임 y 로 읽는다).
3. **끝단 서브픽셀**이 사실상 없었다 — 측정 창이 끝단에서 딱 끝나고 평활화가 창 밖을 0 으로
   채워, 창 경계의 가짜 에지를 끝단으로 잡았다. 길이가 '창 폭 −1px' 정수로 고정(단일 경로도).
   → 창을 끝단 바깥 배경까지 넓히고 끝값으로 채운다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from aivis_types import ItemMaster  # noqa: E402

from vision.calib.fiducial import FiducialConfig, measure_scale  # noqa: E402
from vision.calib.lens import LensCalibration  # noqa: E402
from vision.multi import inspect_batch  # noqa: E402
from vision.multi.segment import segment_tubes  # noqa: E402
from vision.pipeline import InspectionPipeline  # noqa: E402
from vision.preprocess.area import InspectArea  # noqa: E402
from vision.tools.gen_synthetic import make_multi_image  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_lens_helpers", Path(__file__).with_name("test_lens_calib.py"))
_tl = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_tl)

SCALE = 0.25          # mm/px (합성)
TRUE_MM = 125.0       # make_multi_image 튜브 500px


def _item(**kw) -> ItemMaster:
    base = dict(item_code="HP12", item_name="t", ref_length_mm=TRUE_MM, tol_plus_mm=0.5,
                tol_minus_mm=0.5, px_to_mm_scale=SCALE, expected_count=5)
    base.update(kw)
    return ItemMaster(**base)


def _lengths(batch):
    return [t.length_mm for t in batch.tubes]


# --- 3. 끝단 서브픽셀 ----------------------------------------------------------
def _flat_tube(length_px: float, *, h: int = 200, w: int = 900, up: int = 10) -> np.ndarray:
    """끝단이 화소 사이에 걸린 평평한 막대 — 10배로 그려 줄여 분수 위치를 만든다."""
    big = np.full((h * up, w * up, 3), 30, np.uint8)
    x0 = 200.0
    xs, xe = int(round(x0 * up)), int(round((x0 + length_px) * up))
    big[(h // 2 - 30) * up:(h // 2 + 30) * up, xs:xe] = 200
    return cv2.resize(big, (w, h), interpolation=cv2.INTER_AREA)


def test_end_detection_is_subpixel():
    """길이가 화소 사이 값이어도 그 값으로 잰다(종전: 창 폭 −1px 정수로 고정).

    허용 ±0.25px: 기울기 정점의 포물선 보간은 끝단이 화소 안 어디에 있느냐에 따라 최대
    0.2px 쯤 치우친다(S자 — 알려진 성질, 실제 광학 0.072mm/px 에서 0.014mm). 실물 MSA 에서
    확인할 남은 항목이다. 여기서 지키는 것은 '화소 사이 길이를 구분한다' 는 것.
    """
    lengths = [499.7, 500.0, 500.2, 500.3, 500.5, 500.8]
    meas = []
    for length_px in lengths:
        item = _item(expected_count=1, ref_length_mm=length_px * SCALE)
        r = InspectionPipeline().run(_flat_tube(length_px), item)
        assert r.length.meas_length_mm is not None
        assert r.length.meas_length_mm == pytest.approx(length_px * SCALE, abs=0.25 * SCALE), \
            f"{length_px}px → {r.length.meas_length_mm / SCALE:.3f}px"
        meas.append(r.length.meas_length_mm)
    assert meas == sorted(meas) and len(set(meas)) == len(meas), \
        f"화소 사이 길이를 구분하지 못한다: {meas}"


def test_bundle_lengths_are_not_quantised_to_box_width():
    """다발: 가장 긴 튜브도 끝단 바깥 배경이 crop 에 들어와 참길이로 잰다(종전 124.75 = 499px)."""
    img, _ = make_multi_image(5, w=800)
    b = inspect_batch(img, _item(), expected_count=5)
    assert all(v == pytest.approx(TRUE_MM, abs=0.05) for v in _lengths(b)), _lengths(b)


def test_tube_cut_by_frame_edge_reports_no_edge():
    """끝단이 화면 밖이면 가짜 길이 대신 '끝단 미검출'(창 경계를 끝단으로 잡지 않는다)."""
    img = np.full((200, 600, 3), 30, np.uint8)
    img[70:130, 0:450] = 200          # 왼쪽 끝이 화면 밖
    r = InspectionPipeline().run(img, _item(expected_count=1))
    assert r.length.edge_detected is False and r.length.meas_length_mm is None


# --- 1. 렌즈 왜곡 보정은 전체 프레임에서 한 번 ------------------------------------
def _distorted_bundle(k1: float):
    img, _ = make_multi_image(5, w=800)
    h, w = img.shape[:2]
    H, W = 600, 1100
    ideal = np.full((H, W, 3), 32, np.uint8)
    ideal[300:300 + h, 60:60 + w] = img        # 아래·왼쪽으로 치우침 — 왜곡이 비대칭
    K, D = _tl._K(W, H), np.array([k1, 0, 0, 0, 0], np.float64)
    return ideal, _tl._simulate_camera(ideal, K, D), LensCalibration(
        camera_matrix=K, dist_coeffs=D, image_size=(W, H))


def test_bundle_lens_correction_on_full_frame_restores_lengths():
    ideal, cam, calib = _distorted_bundle(-0.20)
    item = _item()
    truth = _lengths(inspect_batch(ideal, item, expected_count=5))
    fixed = _lengths(inspect_batch(cam, item, expected_count=5,
                                   pipeline=InspectionPipeline(lens=calib)))
    raw = _lengths(inspect_batch(cam, item, expected_count=5, pipeline=InspectionPipeline()))
    assert None not in fixed, f"보정 후 끝단을 잃은 튜브가 있다: {fixed}"
    err_fixed = max(abs(a - b) for a, b in zip(fixed, truth))
    err_raw = max(abs(a - b) for a, b in zip(raw, truth))
    # 남는 차이는 합성 영상 보간 몫(test_lens_calib 참조).
    assert err_fixed <= 0.25, f"보정 후 오차 {err_fixed:.3f}mm"
    assert err_raw >= 5 * err_fixed, f"보정 없이 {err_raw:.3f}mm — 보정이 효과가 있어야 한다"


def test_crop_level_lens_correction_was_wrong():
    """종전 방식(왜곡 프레임에서 나누고 crop 마다 보정)이 틀렸다는 것을 고정한다."""
    ideal, cam, calib = _distorted_bundle(-0.20)
    item = _item()
    truth = _lengths(inspect_batch(ideal, item, expected_count=5))
    pipe = InspectionPipeline(lens=calib)
    old = [pipe.run_with_geometry(r.crop(cam), item)[0].length.meas_length_mm
           for r in segment_tubes(cam, expected_count=5)]
    bad = [o is None or abs(o - t) > 0.5 for o, t in zip(old, truth)]
    assert all(bad), f"crop 단위 보정이 맞게 나왔다면 전제가 틀렸다: {old}"


# --- 2. 기준자는 전체 프레임에서 한 번 -------------------------------------------
def _ruler_scene(scale_true: float = SCALE, pitch_mm: float = 5.0, ruler_x0: int = 30):
    """튜브 5개 다발 + 위쪽 기준자 띠(밝은 띠에 어두운 마크, 제품보다 길게)."""
    img, _ = make_multi_image(5, w=800)
    h, w = img.shape[:2]
    frame = np.full((h + 120, w, 3), 32, np.uint8)
    frame[120:120 + h] = img
    frame[20:80, :] = 230
    pitch_px = pitch_mm / scale_true
    x = float(ruler_x0)
    while x < w - 20:
        cv2.circle(frame, (int(round(x)), 50), 6, (20, 20, 20), -1)
        x += pitch_px
    fid = FiducialConfig(pitch_mm=pitch_mm, roi=(0, 20, w, 60))
    # 기준자 띠는 튜브로 세지 않게 검사 영역 밖에 둔다(읽기는 전체 프레임에서 한다).
    area = InspectArea(0.0, 100 / frame.shape[0], 1.0, 1.0)
    return frame, fid, area


def test_bundle_uses_ruler_read_from_full_frame():
    frame, fid, area = _ruler_scene()
    wrong = _item(px_to_mm_scale=0.30)   # 저장된 계수가 틀려도 기준자가 맞으면 맞게 잰다
    b = inspect_batch(frame, wrong, expected_count=5, area=area,
                      pipeline=InspectionPipeline(fiducial=fid))
    assert b.count_detected == 5
    assert all(v == pytest.approx(TRUE_MM, abs=0.05) for v in _lengths(b)), _lengths(b)
    assert not any(t.review_flag for t in b.tubes), "기준자 읽기 실패로 재확인이 걸리면 안 된다"
    assert b.batch_verdict == "OK"


def test_crop_level_ruler_read_was_failing():
    """종전 방식(crop 에서 기준자 찾기)은 범위 밖 → 튜브 전부 NG + 재확인이었다."""
    frame, fid, area = _ruler_scene()
    pipe = InspectionPipeline(fiducial=fid)
    old = [pipe.run_with_geometry(r.crop(frame), _item())[0]
           for r in segment_tubes(frame, expected_count=5, area=area)]
    assert all(v.review_flag and v.final_verdict == "NG" for v in old)


def test_bundle_unreadable_ruler_is_recorded_per_tube():
    """기준자가 안 읽히면(띠에 마크 없음) 단일 경로와 같이 튜브마다 NG + 재확인 — 멈추지 않는다."""
    frame, _fid, area = _ruler_scene()
    empty = FiducialConfig(pitch_mm=5.0, roi=(0, 100, 50, 15))   # 마크 없는 곳
    b = inspect_batch(frame, _item(), expected_count=5, area=area,
                      pipeline=InspectionPipeline(fiducial=empty))
    assert b.count_detected == 5
    assert all(t.review_flag and t.final_verdict == "NG" for t in b.tubes)


def test_vertical_bundle_reads_ruler_along_y():
    """세로 다발: 기준자도 세로로 두고 프레임 y 로 읽는다. 가로와 같은 길이가 나와야 한다."""
    frame, _fid, _area = _ruler_scene()
    rot = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    W = frame.shape[1]
    fid_v = FiducialConfig(pitch_mm=5.0, roi=(20, 0, 60, W))      # 회전 후 기준자 = 왼쪽 세로 띠
    area_v = InspectArea(100 / frame.shape[0], 0.0, 1.0, 1.0)
    b = inspect_batch(rot, _item(px_to_mm_scale=0.30), expected_count=5, axis="vertical",
                      area=area_v, pipeline=InspectionPipeline(fiducial=fid_v))
    assert b.count_detected == 5
    assert all(v == pytest.approx(TRUE_MM, abs=0.05) for v in _lengths(b)), _lengths(b)
    assert not any(t.review_flag for t in b.tubes)


def test_ruler_centers_are_in_frame_coordinates():
    """기준자 띠가 x=0 에서 시작하지 않아도 마크 위치는 프레임 좌표(끝단 x 와 같은 좌표계)."""
    frame, _fid, _area = _ruler_scene(ruler_x0=130)
    s = measure_scale(frame, pitch_mm=5.0, roi=(100, 20, frame.shape[1] - 100, 60))
    assert s.centers_px[0] == pytest.approx(130.0, abs=0.5)


def test_worker_reuses_its_pipeline_for_bundles(tmp_path, monkeypatch):
    """워커 다발 경로는 자기 파이프라인을 쓴다 — 프레임마다 새로 만들면 렌즈 보정 맵을
    매번 다시 만든다(고해상도에서 수십~수백 ms)."""
    import vision.multi.batch as batch_mod
    from vision.worker.runner import Worker

    spec = importlib.util.spec_from_file_location(
        "_tw_bundle_cal", Path(__file__).with_name("test_worker.py"))
    tw = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(tw)

    seen = []
    real = batch_mod.inspect_batch

    def spy(*a, **kw):
        seen.append(kw.get("pipeline"))
        return real(*a, **kw)

    import vision.worker.runner as runner_mod
    monkeypatch.setattr(runner_mod, "inspect_batch", spy)
    backend = tw.FakeBackend(master_requires_auth=True)
    w = Worker(tw._cfg(tmp_path, max_iterations=1), client=tw._client(backend))
    assert w.startup() is True
    w.item = w.item.model_copy(update={"expected_count": 5})
    img, _ = make_multi_image(5, w=800)

    class Cam:
        def grab(self):
            return img.copy()

        def configure(self, recipe):
            pass

        def close(self):
            pass

    from vision.acquisition import AcquisitionService
    w.acq = AcquisitionService(camera=Cam(), max_retries=1, grab_timeout_s=5)
    w.run_once()
    w.run_once()
    assert seen and all(p is w.pipeline for p in seen)
    w.shutdown()
