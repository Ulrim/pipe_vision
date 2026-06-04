"""M3 길이 측정 테스트 — 서브픽셀, 공차 분기, 끝단검출 실패, MSA 반복성."""
from __future__ import annotations

import numpy as np
from aivis_types import Verdict

from vision.length import measure_length
from vision.preprocess import preprocess
from vision.tools.gen_synthetic import make_image


def _measure(img, item):
    pre = preprocess(img)
    roi = pre.length_roi.crop(pre.gray_corrected)
    return measure_length(roi, item)


def test_ok_within_tolerance(item):
    img, _ = make_image("OK")
    r = _measure(img, item)
    assert r.edge_detected
    assert r.meas_length_mm is not None
    assert r.length_verdict == Verdict.OK.value
    assert abs(r.deviation_mm) <= item.tol_plus_mm


def test_len_plus_is_ng(item):
    img, _ = make_image("LEN_PLUS")  # +80px → +20mm @0.25
    r = _measure(img, item)
    assert r.edge_detected
    assert r.deviation_mm > item.tol_plus_mm
    assert r.length_verdict == Verdict.NG.value


def test_len_minus_is_ng(item):
    img, _ = make_image("LEN_MINUS")
    r = _measure(img, item)
    assert r.deviation_mm < -item.tol_minus_mm
    assert r.length_verdict == Verdict.NG.value


def test_edge_detection_failure_path(item):
    # 평탄(대비 없음) 이미지 → 끝단 검출 실패.
    flat = np.full((300, 800, 3), 30, dtype=np.uint8)
    r = measure_length(flat[:, :, 0].copy(), item)
    assert r.edge_detected is False
    assert r.meas_length_mm is None
    assert r.deviation_mm is None
    assert r.length_verdict == Verdict.NG.value


def test_scale_from_item_master(item):
    """px_to_mm_scale 가 item_master 에서 적용되는지(하드코딩 아님)."""
    img, _ = make_image("OK")
    r1 = _measure(img, item)
    item2 = item.model_copy(update={"px_to_mm_scale": item.px_to_mm_scale * 2})
    r2 = _measure(img, item2)
    assert abs(r2.meas_length_mm - 2 * r1.meas_length_mm) < 0.5


def test_msa_repeatability(item):
    """동일 샘플 반복 측정 → 결정적(편차 0). MSA 반복성."""
    img, _ = make_image("OK")
    vals = [_measure(img.copy(), item).meas_length_mm for _ in range(30)]
    assert max(vals) - min(vals) == 0.0  # 결정적


def test_proc_time_within_budget(item):
    img, _ = make_image("OK")
    pre = preprocess(img)
    roi = pre.length_roi.crop(pre.gray_corrected)
    r = measure_length(roi, item)
    assert r.proc_time_ms <= 80  # M3 예산
