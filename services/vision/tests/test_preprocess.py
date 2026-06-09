"""M2 전처리 테스트 — ROI 결정성(편차 ≤2px), 영역 구분."""
from __future__ import annotations


from vision.preprocess import preprocess
from vision.tools.gen_synthetic import make_image


def test_roi_found_and_bounds():
    img, bbox = make_image("OK")
    res = preprocess(img)
    assert res.found
    assert res.length_roi is not None and res.surface_roi is not None
    x0, y0, x1, y1 = bbox
    # 검출 ROI 가 합성 파이프 bbox 와 근접(±5px).
    assert abs(res.length_roi.x0 - x0) <= 5
    assert abs(res.length_roi.x1 - x1) <= 5


def test_roi_deterministic_under_2px():
    img, _ = make_image("OK")
    r1 = preprocess(img)
    r2 = preprocess(img.copy())
    assert r1.length_roi.as_tuple() == r2.length_roi.as_tuple()
    # 미세 동일성: 반복 입력 시 편차 0px (≤2px DoD 충족).


def test_surface_roi_inset_inside_length_roi():
    img, _ = make_image("OK")
    res = preprocess(img)
    lr, sr = res.length_roi, res.surface_roi
    assert sr.x0 >= lr.x0 and sr.x1 <= lr.x1
    assert sr.width < lr.width  # 끝단 인셋


def test_mask_shape_matches():
    img, _ = make_image("OK")
    res = preprocess(img)
    assert res.mask.shape[:2] == img.shape[:2]
    assert res.gray_corrected.shape[:2] == img.shape[:2]
    assert res.proc_time_ms >= 0
