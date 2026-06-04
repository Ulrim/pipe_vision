"""M4 표면 판정 테스트 — 항목별 점수, 임계 분기(ItemMaster), 결정성."""
from __future__ import annotations

from aivis_types import DefectCode, Verdict

from vision.preprocess import preprocess
from vision.surface import analyze_surface
from vision.tools.gen_synthetic import make_image


def _analyze(cls, item):
    img, _ = make_image(cls)
    pre = preprocess(img)
    region = pre.surface_roi.crop(img)
    mask = pre.surface_roi.crop(pre.mask)
    return analyze_surface(region, item, mask=mask)


def test_ok_surface_clean(item):
    r = _analyze("OK", item)
    assert r.surface_verdict == Verdict.OK.value
    assert r.defect_codes == []
    for s in (r.oil_score, r.discolor_score, r.scratch_score):
        assert 0.0 <= s <= 1.0


def test_scratch_detected(item):
    r = _analyze("SCR", item)
    assert r.scratch_score > item.scratch_threshold
    assert DefectCode.SCR.value in r.defect_codes
    assert r.surface_verdict == Verdict.NG.value


def test_oil_detected(item):
    r = _analyze("OIL", item)
    assert r.oil_score > item.oil_threshold
    assert DefectCode.OIL.value in r.defect_codes


def test_discolor_detected(item):
    r = _analyze("DIS", item)
    assert r.discolor_score > item.discolor_threshold
    assert DefectCode.DIS.value in r.defect_codes


def test_threshold_from_item_master_branch(item):
    """임계값을 올리면 동일 이미지가 OK 로 분기(하드코딩 아님)."""
    img, _ = make_image("SCR")
    pre = preprocess(img)
    region = pre.surface_roi.crop(img)
    mask = pre.surface_roi.crop(pre.mask)
    low = analyze_surface(region, item, mask=mask)
    assert DefectCode.SCR.value in low.defect_codes
    high_item = item.model_copy(update={"scratch_threshold": 0.99})
    high = analyze_surface(region, high_item, mask=mask)
    assert DefectCode.SCR.value not in high.defect_codes


def test_deterministic(item):
    a = _analyze("DIS", item)
    b = _analyze("DIS", item)
    assert (a.oil_score, a.discolor_score, a.scratch_score) == (
        b.oil_score,
        b.discolor_score,
        b.scratch_score,
    )
    assert a.proc_time_ms >= 0
