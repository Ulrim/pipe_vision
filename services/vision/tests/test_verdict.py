"""M5 종합 판정 테스트 — 통합 OK/NG, MULTI, confidence, review_flag, 결정성."""
from __future__ import annotations

from aivis_types import (
    DefectCode,
    ItemMaster,
    LengthResult,
    SurfaceResult,
    Verdict,
)

from vision.verdict import combine_verdict


def _item():
    return ItemMaster(
        item_code="HP12",
        item_name="x",
        ref_length_mm=125.0,
        tol_plus_mm=3.0,
        tol_minus_mm=3.0,
        px_to_mm_scale=0.25,
        oil_threshold=0.30,
        discolor_threshold=0.20,
        scratch_threshold=0.15,
    )


def _len(verdict=Verdict.OK, dev=0.0, edge=True):
    return LengthResult(
        ref_length_mm=125.0,
        meas_length_mm=125.0 + dev if edge else None,
        deviation_mm=dev if edge else None,
        length_verdict=verdict,
        edge_detected=edge,
    )


def _surf(verdict=Verdict.OK, codes=None, oil=0.05, dis=0.05, scr=0.05):
    return SurfaceResult(
        oil_score=oil,
        discolor_score=dis,
        scratch_score=scr,
        surface_verdict=verdict,
        defect_codes=codes or [],
    )


def test_all_ok():
    v = combine_verdict(_len(), _surf(), _item())
    assert v.final_verdict == Verdict.OK.value
    assert v.defect_codes == []


def test_single_length_ng():
    v = combine_verdict(_len(Verdict.NG, dev=10.0), _surf(), _item())
    assert v.final_verdict == Verdict.NG.value
    assert DefectCode.LEN.value in v.defect_codes
    assert DefectCode.MULTI.value not in v.defect_codes


def test_multi_when_two_defects():
    length = _len(Verdict.NG, dev=10.0)
    surf = _surf(Verdict.NG, codes=[DefectCode.SCR], scr=0.9)
    v = combine_verdict(length, surf, _item())
    assert DefectCode.LEN.value in v.defect_codes
    assert DefectCode.SCR.value in v.defect_codes
    assert DefectCode.MULTI.value in v.defect_codes


def test_two_surface_defects_multi():
    surf = _surf(Verdict.NG, codes=[DefectCode.OIL, DefectCode.DIS], oil=0.9, dis=0.9)
    v = combine_verdict(_len(), surf, _item())
    assert DefectCode.MULTI.value in v.defect_codes


def test_confidence_deterministic():
    a = combine_verdict(_len(), _surf(), _item())
    b = combine_verdict(_len(), _surf(), _item())
    assert a.confidence == b.confidence
    assert 0.0 <= a.confidence <= 1.0


def test_review_flag_on_edge_failure():
    v = combine_verdict(_len(Verdict.NG, edge=False), _surf(), _item())
    assert v.review_flag is True


def test_review_flag_near_length_tolerance():
    # dev=2.9, tol=3.0 → 경계(±15% 밴드 = 0.45 안).
    v = combine_verdict(_len(Verdict.OK, dev=2.9), _surf(), _item())
    assert v.review_flag is True


def test_review_flag_near_surface_threshold():
    # scr score 0.15 == threshold → 경계.
    surf = _surf(scr=0.15)
    v = combine_verdict(_len(), surf, _item())
    assert v.review_flag is True
