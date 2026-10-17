"""검사 영역(ROI, AIVIS_ROI) — 2026-10-16.

화면에 보이는 사각형과 실제 판정이 같아야 한다.
1. 설정: 화면 비율 "x0,y0,x1,y1". 잘못 적으면 화면 전체(종전 동작).
2. 검사: 영역 밖 물체는 튜브 나누기·단면 세기·단일 제품 찾기에 끼지 않는다. 좌표는 원본
   프레임 기준(라벨링 자르기·오버레이가 그대로 맞는다).
3. 결과 사진: 노란 점선 사각형, 영역 밖은 어둡게, 영역 안·원본은 그대로.
   라벨에 가로x세로(px), 길이 검사 모드에서만 mm 근사값도(2026-10-17).
4. 워커: AIVIS_ROI 하나로 트리거·검사·사진이 같은 영역을 쓴다.
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

from vision.acquisition import AcquisitionService, VisionTrigger, create_trigger  # noqa: E402
from vision.imaging.draw import ROI_COLOR, inspect_area_label  # noqa: E402
from vision.imaging.save import (  # noqa: E402
    render_batch_overlay,
    render_count_overlay,
    render_overlay,
)
from vision.multi import inspect_batch  # noqa: E402
from vision.multi.bundle import count_bundle  # noqa: E402
from vision.multi.segment import segment_tubes  # noqa: E402
from vision.pipeline import InspectionPipeline  # noqa: E402
from vision.preprocess.area import InspectArea, area_rect, parse_area  # noqa: E402
from vision.tools.gen_synthetic import make_multi_image  # noqa: E402
from vision.worker.runner import Worker  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_test_worker_stubs_area", Path(__file__).with_name("test_worker.py"))
_tw = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_tw)
FakeBackend, _cfg, _client = _tw.FakeBackend, _tw._cfg, _tw._client

BG = 32


def _bundle_with_side_object():
    """튜브 5개 다발 + 오른쪽 같은 줄에 놓인 물체(영역 밖). (프레임, 영역, 다발 bbox)."""
    img, boxes = make_multi_image(5, w=800)
    h, w = img.shape[:2]
    frame = np.full((h, w + 300, 3), BG, np.uint8)
    frame[:, :w] = img
    frame[60:200, w + 60:w + 260] = 200
    area = InspectArea(0.0, 0.0, (w + 20) / (w + 300), 1.0)
    return frame, area, boxes


def _crate(k=32, cols=8, rows=4, pitch=60, r_bore=14):
    hgt, wid = rows * pitch + 80, cols * pitch + 80
    img = np.full((hgt, wid, 3), 190, np.uint8)
    n = 0
    for i in range(rows):
        for j in range(cols):
            if n >= k:
                return img
            cx, cy = 40 + j * pitch + pitch // 2, 40 + i * pitch + pitch // 2
            cv2.circle(img, (cx, cy), r_bore + 10, (215, 215, 215), -1)
            cv2.circle(img, (cx, cy), r_bore, (25, 25, 25), -1)
            n += 1
    return img


def _crate_with_neighbor():
    """크레이트 32개 + 오른쪽 옆 크레이트 단면 3개(영역 밖)."""
    crate = _crate(32)
    ch, cw = crate.shape[:2]
    frame = np.full((ch + 200, cw + 260, 3), 190, np.uint8)
    frame[100:100 + ch, 20:20 + cw] = crate
    for k in range(3):
        cx, cy = cw + 120, 160 + k * 70
        cv2.circle(frame, (cx, cy), 24, (215, 215, 215), -1)
        cv2.circle(frame, (cx, cy), 14, (25, 25, 25), -1)
    fh, fw = frame.shape[:2]
    area = InspectArea(10 / fw, 90 / fh, (30 + cw) / fw, (110 + ch) / fh)
    return frame, area


def _item(**kw):
    base = dict(item_code="HP12", item_name="t", ref_length_mm=250.0, tol_plus_mm=0.5,
                tol_minus_mm=0.5, px_to_mm_scale=0.5, expected_count=5)
    base.update(kw)
    return ItemMaster(**base)


# --- 1. 설정 -----------------------------------------------------------------
def test_parse_area():
    assert parse_area("0.05,0.20,0.95,0.80") == (0.05, 0.2, 0.95, 0.8)
    assert parse_area("0.95,0.8,0.05,0.2") == (0.05, 0.2, 0.95, 0.8), "순서가 바뀌어도 된다"
    assert parse_area("-1,0,2,1") == (0.0, 0.0, 1.0, 1.0), "화면 밖은 잘라 낸다"
    for bad in ("", "junk", "0,0,1", "0.5,0.5,0.51,0.9", None):
        assert parse_area(bad) == (0.0, 0.0, 1.0, 1.0)


def test_from_env_new_name_wins_and_legacy_still_read(monkeypatch):
    monkeypatch.delenv("AIVIS_ROI", raising=False)
    monkeypatch.delenv("AIVIS_VISION_ROI", raising=False)
    assert InspectArea.from_env().full
    monkeypatch.setenv("AIVIS_VISION_ROI", "0.1,0.1,0.9,0.9")
    assert InspectArea.from_env().fractions == (0.1, 0.1, 0.9, 0.9)
    monkeypatch.setenv("AIVIS_ROI", "0.2,0.3,0.8,0.7")
    assert InspectArea.from_env().fractions == (0.2, 0.3, 0.8, 0.7)


def test_pixels_and_full_frame_means_nothing_to_cut():
    a = InspectArea(0.1, 0.2, 0.9, 0.8)
    r = a.pixels(1000, 500)
    assert (r.x0, r.y0, r.x1, r.y1) == (100, 100, 900, 400)
    frame = np.zeros((500, 1000, 3), np.uint8)
    assert area_rect(InspectArea(), frame) is None and area_rect(None, frame) is None
    assert area_rect(a, frame) == r


# --- 2. 검사 -----------------------------------------------------------------
def test_segment_ignores_object_beside_bundle():
    frame, area, boxes = _bundle_with_side_object()
    without = segment_tubes(frame, expected_count=None)
    # 영역이 없으면 옆 물체가 튜브 상자를 오른쪽으로 늘린다 — 길이가 틀리게 잰다.
    assert max(t.x1 for t in without) > 900
    inside = segment_tubes(frame, expected_count=None, area=area)
    assert len(inside) == 5
    assert all(t.x1 <= 660 for t in inside), "영역 밖 물체가 상자에 끼면 안 된다"
    # 좌표는 원본 프레임 기준 — 정답 상자와 같은 자리.
    assert abs(inside[0].x0 - boxes[0][0]) <= 2 and abs(inside[0].x1 - boxes[0][2]) <= 2


def test_inspect_batch_uses_area_and_frame_coordinates():
    frame, area, _ = _bundle_with_side_object()
    item = _item()
    b = inspect_batch(frame, item, expected_count=5, area=area)
    assert b.count_detected == 5
    r = area.pixels(frame.shape[1], frame.shape[0])
    for t in b.tubes:
        x0, y0, x1, y1 = t.bbox
        assert r.x0 <= x0 < x1 <= r.x1 and r.y0 <= y0 < y1 <= r.y1
    lens = {round(t.length_mm or 0) for t in b.tubes}
    assert len(lens) == 1, f"영역 안 튜브는 모두 같은 길이로 재야 한다: {lens}"


def test_inspect_batch_area_offset_is_applied():
    """영역이 화면 가운데일 때도 bbox 는 원본 프레임 좌표(영역 기준 좌표 아님)."""
    img, boxes = make_multi_image(5, w=800)
    h, w = img.shape[:2]
    frame = np.full((h + 200, w + 200, 3), BG, np.uint8)
    frame[100:100 + h, 100:100 + w] = img
    area = InspectArea(60 / (w + 200), 60 / (h + 200), (w + 140) / (w + 200), (h + 140) / (h + 200))
    b = inspect_batch(frame, _item(), expected_count=5, area=area)
    assert b.count_detected == 5
    assert abs(b.tubes[0].bbox[0] - (boxes[0][0] + 100)) <= 2
    assert abs(b.tubes[0].bbox[1] - (boxes[0][1] + 100)) <= 6


def test_count_bundle_counts_only_inside_area():
    frame, area = _crate_with_neighbor()
    assert count_bundle(frame).count == 35, "영역 없으면 옆 크레이트 3개까지 센다"
    res = count_bundle(frame, area=area)
    assert res.count == 32
    r = area.pixels(frame.shape[1], frame.shape[0])
    assert all(r.x0 <= d.cx < r.x1 and r.y0 <= d.cy < r.y1 for d in res.detections)


def test_single_pipeline_picks_product_inside_area():
    """화면에 제품이 둘이면(큰 것은 영역 밖) 영역 안의 제품을 잰다."""
    frame = np.full((400, 1200, 3), 20, np.uint8)
    frame[60:120, 100:500] = 200      # 영역 안(작은 것)
    frame[220:360, 600:1150] = 210    # 영역 밖(큰 것)
    area = InspectArea(0.0, 0.0, 0.5, 0.5)
    pipe = InspectionPipeline()
    item = _item(expected_count=1, px_to_mm_scale=1.0, ref_length_mm=400.0)
    _vr, span = pipe.run_with_geometry(frame, item, area=area)
    assert span is not None
    assert span.right_x < 600, "영역 밖의 큰 제품을 잡으면 안 된다"
    _vr2, span2 = pipe.run_with_geometry(frame, item)
    assert span2 is not None and span2.left_x >= 590, "영역 없으면 가장 큰 제품(종전 동작)"


# --- 3. 결과 사진 -------------------------------------------------------------
def _is_roi_color(px) -> bool:
    return all(abs(int(px[i]) - ROI_COLOR[i]) < 50 for i in range(3))


def test_batch_overlay_draws_area_and_dims_outside():
    frame, area, _ = _bundle_with_side_object()
    b = inspect_batch(frame, _item(), expected_count=5, area=area)
    out = render_batch_overlay(frame, b, area=area)
    r = area.pixels(frame.shape[1], frame.shape[0])
    # 경계 점선(오른쪽 세로선) 어딘가는 노랑.
    col = out[:, r.x1 - 1]
    assert any(_is_roi_color(p) for p in col), "검사 영역 경계가 안 그려졌다"
    # 영역 밖 물체는 어둡게, 원본 프레임은 그대로.
    assert int(out[150, 1000].mean()) < int(frame[150, 1000].mean()) - 40
    assert int(frame[150, 1000].mean()) == 200


def test_overlay_without_area_unchanged():
    frame, _, _ = _bundle_with_side_object()
    b = inspect_batch(frame, _item(), expected_count=5)
    assert np.array_equal(render_batch_overlay(frame, b),
                          render_batch_overlay(frame, b, area=InspectArea()))


def test_count_and_single_overlays_draw_area():
    frame, area = _crate_with_neighbor()
    res = count_bundle(frame, area=area)
    out = render_count_overlay(frame, res, expected=32, area=area)
    r = area.pixels(frame.shape[1], frame.shape[0])
    assert any(_is_roi_color(p) for p in out[r.y1 - 1, r.x0:r.x1])
    # 옆 크레이트 자리는 어둡고 원(검출 표시)도 없다.
    assert int(out[160, frame.shape[1] - 140].mean()) < 150

    single = np.full((300, 800, 3), 20, np.uint8)
    single[120:180, 150:650] = 200
    sa = InspectArea(0.05, 0.2, 0.95, 0.9)
    pipe = InspectionPipeline()
    item = _item(expected_count=1)
    vr, span = pipe.run_with_geometry(single, item, area=sa)
    so = render_overlay(single, vr, item=item, length_span=span, area=sa)
    sr = sa.pixels(800, 300)
    assert any(_is_roi_color(p) for p in so[sr.y0:sr.y1, sr.x0])


# --- 4. 워커 -----------------------------------------------------------------
class OneFrameCam:
    def __init__(self, frame):
        self.frame = frame

    def preview(self, width=320):
        from vision.acquisition.camera import _to_preview

        return _to_preview(self.frame, width)

    def grab(self):
        return self.frame.copy()

    def configure(self, recipe):
        pass

    def close(self):
        pass


def test_worker_env_area_drives_inspection_and_result_image(tmp_path, monkeypatch):
    frame, area, _ = _bundle_with_side_object()
    monkeypatch.setenv("AIVIS_ROI", area.text())
    backend = FakeBackend(master_requires_auth=True)
    w = Worker(_cfg(tmp_path, max_iterations=1), client=_client(backend))
    assert w.area == InspectArea.parse(area.text())
    assert w.startup() is True
    w.item = w.item.model_copy(update={"expected_count": 5})
    cam = OneFrameCam(frame)
    w.camera = cam
    w.acq = AcquisitionService(camera=cam, max_retries=1, grab_timeout_s=5)
    assert w.run_once() is True

    rows = backend.posted
    assert len(rows) == 5 and {r["tube_index"] for r in rows} == set(range(5)), \
        "영역 밖 물체를 튜브로 세지 않는다"
    r = area.pixels(frame.shape[1], frame.shape[0])
    for row in rows:
        x0, y0, x1, y1 = row["tube_bbox"]
        assert x1 <= r.x1, "tube_bbox 는 원본 프레임 좌표, 영역 안"
    result = cv2.imread(str(Path(tmp_path / "images") / rows[0]["result_image_path"]))
    raw = cv2.imread(str(Path(tmp_path / "images") / rows[0]["raw_image_path"]))
    assert result is not None and raw is not None
    assert int(result[150, 1000].mean()) < int(raw[150, 1000].mean()) - 40, \
        "결과 사진에서 영역 밖이 어두워야 한다(원본은 그대로)"
    assert abs(int(raw[150, 1000].mean()) - 200) <= 6
    w.shutdown()


def test_trigger_uses_same_area(monkeypatch):
    monkeypatch.setenv("AIVIS_TRIGGER", "vision")
    monkeypatch.setenv("AIVIS_ROI", "0.9,0.9,0.1,0.1")
    cam = OneFrameCam(np.zeros((100, 100, 3), np.uint8))
    t = create_trigger(0.0, camera=cam)
    assert isinstance(t, VisionTrigger) and t.roi == (0.1, 0.1, 0.9, 0.9)
    t2 = create_trigger(0.0, camera=cam, roi=(0.2, 0.2, 0.8, 0.8))
    assert t2.roi == (0.2, 0.2, 0.8, 0.8), "워커가 넘긴 영역이 우선"


@pytest.mark.parametrize("spec", ["0,0,1,1", ""])
def test_full_frame_worker_draws_nothing(spec, monkeypatch):
    monkeypatch.setenv("AIVIS_ROI", spec)
    assert InspectArea.from_env().full


# --- 5. 라벨: 가로x세로 ---------------------------------------------------------
def test_area_label_px_and_optional_mm():
    assert inspect_area_label(3648, 1824) == "ROI 3648x1824px"
    assert inspect_area_label(3648, 1824, 0.072) == "ROI 3648x1824px ~263x131mm"
    assert inspect_area_label(800, 300, 0.05) == "ROI 800x300px ~40.0x15.0mm"
    assert inspect_area_label(800, 300, 0) == "ROI 800x300px", "계수가 없으면 px 만"


def _record_area_draws(monkeypatch):
    import vision.imaging.save as save_mod

    calls = []
    real = save_mod.draw_inspect_area

    def rec(canvas, rect, **kw):
        calls.append((rect, kw.get("mm_per_px")))
        return real(canvas, rect, **kw)

    monkeypatch.setattr(save_mod, "draw_inspect_area", rec)
    return calls


def test_worker_mm_only_in_length_mode(tmp_path, monkeypatch):
    """mm 는 길이 검사 모드에서만 — 표면·크레이트 스테이션은 카메라·거리가 달라 같은 계수로
    mm 를 내면 틀린 숫자가 된다."""
    frame, area, _ = _bundle_with_side_object()
    monkeypatch.setenv("AIVIS_ROI", area.text())
    calls = _record_area_draws(monkeypatch)
    backend = FakeBackend(master_requires_auth=True)
    w = Worker(_cfg(tmp_path, max_iterations=1), client=_client(backend))
    assert w.startup() is True
    w.item = w.item.model_copy(update={"expected_count": 5})
    cam = OneFrameCam(frame)
    w.camera = cam
    w.acq = AcquisitionService(camera=cam, max_retries=1, grab_timeout_s=5)

    w._active_stage = "CUT_LENGTH"
    w.run_once()
    rect, mm = calls[-1]
    assert rect is not None and mm == float(w.item.px_to_mm_scale)

    w._active_stage = "POST_WASH_SURFACE"
    w.run_once()
    assert calls[-1][1] is None, "표면 모드는 px 만"

    w._active_stage = "CRATE_COUNT"
    w.run_once()
    assert calls[-1][1] is None, "크레이트 개수 모드는 px 만"
    w.shutdown()
