"""길이 측정 투명화(끝단·측정선 오버레이) 테스트 — 사용자 피드백② 대응.

- measure_length_ex 가 끝단 좌표(EdgeEndpoints)를 함께 반환.
- pipeline.run_with_geometry 가 프레임 좌표 LengthSpan 을 산출(범위 검증).
- 단일 결과 오버레이(render_overlay)에 span 을 그리면 이미지가 달라진다.
- 배치 튜브가 프레임 좌표 length_span 을 실어 나르고, 배치 오버레이가 그린다.
모두 합성 이미지로 자립(AIVIS_CAMERA 무관), 결정적.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.imaging import render_batch_overlay, render_overlay  # noqa: E402
from vision.imaging.draw import draw_length_span  # noqa: E402
from vision.length import LengthSpan, measure_length_ex  # noqa: E402
from vision.multi import inspect_batch  # noqa: E402
from vision.pipeline import InspectionPipeline  # noqa: E402
from vision.preprocess import preprocess  # noqa: E402
from vision.tools.gen_synthetic import make_image, make_multi_image  # noqa: E402


# --- measure_length_ex 끝단 좌표 노출 ---
def test_measure_length_ex_returns_endpoints(item):
    img, bbox = make_image("OK")
    pre = preprocess(img)
    roi = pre.length_roi.crop(pre.gray_corrected)
    result, endpoints = measure_length_ex(roi, item)
    assert result.edge_detected is True
    assert endpoints is not None
    # 좌 < 우, ROI 폭 안.
    assert 0.0 <= endpoints.left_x < endpoints.right_x <= roi.shape[1]
    # measure_length(래퍼)과 결과 동일(단일 진실원).
    from vision.length import measure_length

    assert measure_length(roi, item).meas_length_mm == result.meas_length_mm


def test_measure_length_ex_no_endpoints_on_flat(item):
    flat = np.full((300, 800), 30, dtype=np.uint8)
    result, endpoints = measure_length_ex(flat, item)
    assert result.edge_detected is False
    assert endpoints is None


# --- pipeline.run_with_geometry: 프레임 좌표 LengthSpan ---
def test_run_with_geometry_span_in_frame_bounds(item):
    img, (x0, y0, x1, y1) = make_image("OK")
    v, span = InspectionPipeline().run_with_geometry(img, item)
    assert span is not None and span.valid
    h, w = img.shape[:2]
    assert 0 <= span.left_x < span.right_x <= w
    assert 0 <= span.y_top < span.y_bottom <= h
    # 끝단은 합성 파이프 bbox 근방(±30px)이어야 한다(측정 근거 정합).
    assert abs(span.left_x - x0) < 30
    assert abs(span.right_x - x1) < 30


def test_run_with_geometry_deterministic(item):
    img, _ = make_image("OK")
    _, a = InspectionPipeline().run_with_geometry(img, item)
    _, b = InspectionPipeline().run_with_geometry(img.copy(), item)
    assert (a.left_x, a.right_x, a.y_top, a.y_bottom) == (
        b.left_x, b.right_x, b.y_top, b.y_bottom
    )


# --- 단일 오버레이에 측정선 표기 ---
def test_render_overlay_with_span_changes_image(item):
    img, _ = make_image("OK")
    v, span = InspectionPipeline().run_with_geometry(img, item)
    without = render_overlay(img, v, item=item)
    with_span = render_overlay(img, v, item=item, length_span=span)
    assert with_span.shape == without.shape
    # 측정선/끝단 마커가 추가되어 픽셀이 달라진다.
    assert not np.array_equal(without, with_span)


def test_render_overlay_span_none_is_noop(item):
    img, _ = make_image("OK")
    v = InspectionPipeline().run(img, item)
    a = render_overlay(img, v, item=item, length_span=None)
    b = render_overlay(img, v, item=item)
    assert np.array_equal(a, b)


def test_draw_length_span_invalid_is_noop():
    canvas = np.zeros((100, 200, 3), dtype=np.uint8)
    before = canvas.copy()
    draw_length_span(canvas, None, color=(0, 255, 0))
    draw_length_span(
        canvas, LengthSpan(None, None, 10, 40), color=(0, 255, 0)
    )
    assert np.array_equal(canvas, before)  # 아무 것도 그리지 않음.


# --- 배치: 튜브별 프레임 좌표 span + 오버레이 ---
def test_inspect_batch_tubes_carry_frame_span(item):
    img, boxes = make_multi_image(5)
    br = inspect_batch(img, item, expected_count=5)
    h, w = img.shape[:2]
    spanned = 0
    for t in br.tubes:
        span = getattr(t, "length_span", None)
        if span is None:
            continue
        spanned += 1
        bx0, by0, bx1, by1 = t.bbox
        # 프레임 좌표계여야 한다(튜브 bbox 안쪽, 프레임 범위 내).
        assert 0 <= span.left_x < span.right_x <= w
        assert by0 <= span.y_top < span.y_bottom <= by1
        # 끝단이 튜브 bbox x 범위 근방.
        assert bx0 - 5 <= span.left_x <= bx1 + 5
        assert bx0 - 5 <= span.right_x <= bx1 + 5
    assert spanned >= 1  # 최소 한 튜브는 측정선을 갖는다.


def test_render_batch_overlay_with_spans_deterministic(item):
    img, _ = make_multi_image(6, defects={2: "SCR", 4: "LEN_PLUS"})
    br = inspect_batch(img, item, expected_count=6)
    a = render_batch_overlay(img, br)
    b = render_batch_overlay(img, br)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, img)


def test_vertical_axis_span_skipped(item):
    """세로축은 회전 매핑 비자명 → span 생략(None). 개수/판정은 유지."""
    img, _ = make_multi_image(5, axis="vertical")
    br = inspect_batch(img, item, axis="vertical", expected_count=5)
    assert br.count_detected == 5
    assert all(getattr(t, "length_span", None) is None for t in br.tubes)
