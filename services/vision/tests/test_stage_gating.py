"""검사 단계(=모드)별 판정 게이팅.

현장 요구(2026-10-05): "한 번에 다 하려니 NG 가 어떻게 나는지 모르겠다."
→ 한 모드는 한 질문에만 답한다. 길이 모드에서 표면 결함이 NG 사유가 되거나,
표면 모드에서 길이 미측정이 재확인을 강제하면 이 요구가 깨진다.
"""
from __future__ import annotations

import numpy as np
from aivis_types import (
    DefectCode,
    InspectionStage,
    ItemMaster,
    LengthResult,
    SurfaceResult,
    Verdict,
)

from vision.pipeline import InspectionPipeline
from vision.verdict import combine_verdict
from vision.verdict.combine import STAGE_CODES, codes_for_stage


def _item():
    return ItemMaster(
        item_code="HP12", item_name="x", ref_length_mm=125.0,
        tol_plus_mm=3.0, tol_minus_mm=3.0, px_to_mm_scale=0.25,
        oil_threshold=0.30, discolor_threshold=0.20, scratch_threshold=0.15,
    )


def _len_ng():
    return LengthResult(ref_length_mm=125.0, meas_length_mm=130.0,
                        deviation_mm=5.0, length_verdict=Verdict.NG,
                        edge_detected=True)


def _len_missing():
    """끝단 검출 실패 — 길이 모드라면 재확인 대상이 되어야 한다."""
    return LengthResult(ref_length_mm=125.0, meas_length_mm=None,
                        deviation_mm=None, length_verdict=Verdict.NG,
                        edge_detected=False)


def _surf_ng():
    return SurfaceResult(oil_score=0.9, discolor_score=0.05, scratch_score=0.05,
                         surface_verdict=Verdict.NG, defect_codes=[DefectCode.OIL])


# --- 코드표 ---------------------------------------------------------------

def test_every_stage_has_exactly_one_question():
    """모드별 코드 집합은 서로 겹치지 않아야 한다 — 겹치면 다시 섞인다."""
    sets = list(STAGE_CODES.values())
    for i, a in enumerate(sets):
        for b in sets[i + 1:]:
            assert not (a & b), f"{a} ∩ {b}"
    assert STAGE_CODES[InspectionStage.CUT_LENGTH.value] == {DefectCode.LEN}
    assert STAGE_CODES[InspectionStage.CRATE_COUNT.value] == {DefectCode.COUNT}


def test_unknown_or_none_stage_keeps_old_behaviour():
    """옛 호출자(stage 없음)는 종전처럼 전부 본다 — 하위호환."""
    assert DefectCode.LEN in codes_for_stage(None)
    assert DefectCode.OIL in codes_for_stage(None)
    assert codes_for_stage("WHATEVER") == codes_for_stage(None)


# --- combine_verdict ------------------------------------------------------

def test_length_mode_ignores_surface_defects():
    r = combine_verdict(_len_ng(), _surf_ng(), _item(),
                        stage=InspectionStage.CUT_LENGTH)
    assert r.final_verdict == Verdict.NG.value
    assert r.defect_codes == [DefectCode.LEN.value]
    assert DefectCode.MULTI.value not in r.defect_codes, "한 모드에선 복합이 날 수 없다"


def test_surface_mode_ignores_length_defects():
    r = combine_verdict(_len_ng(), _surf_ng(), _item(),
                        stage=InspectionStage.POST_WASH_SURFACE)
    assert r.defect_codes == [DefectCode.OIL.value]


def test_length_mode_with_only_surface_defect_is_ok():
    """표면이 나빠도 길이 모드에서는 양품 — 그 질문의 답이 아니다."""
    ok_len = LengthResult(ref_length_mm=125.0, meas_length_mm=125.1,
                          deviation_mm=0.1, length_verdict=Verdict.OK,
                          edge_detected=True)
    r = combine_verdict(ok_len, _surf_ng(), _item(),
                        stage=InspectionStage.CUT_LENGTH)
    assert r.final_verdict == Verdict.OK.value
    assert r.defect_codes == []


def test_surface_mode_does_not_force_review_for_unmeasured_length():
    """표면 모드에서 길이를 안 쟀다고 전부 재확인으로 보내면 안 된다."""
    clean = SurfaceResult(oil_score=0.02, discolor_score=0.02, scratch_score=0.02,
                          surface_verdict=Verdict.OK, defect_codes=[])
    r = combine_verdict(_len_missing(), clean, _item(),
                        stage=InspectionStage.POST_WASH_SURFACE)
    assert r.final_verdict == Verdict.OK.value
    assert r.review_flag is False
    assert r.confidence is not None and r.confidence >= 0.65


def test_length_mode_still_reviews_edge_failure():
    r = combine_verdict(_len_missing(), _surf_ng(), _item(),
                        stage=InspectionStage.CUT_LENGTH)
    assert r.review_flag is True


def test_no_stage_is_the_old_combined_verdict():
    r = combine_verdict(_len_ng(), _surf_ng(), _item())
    assert set(r.defect_codes) == {
        DefectCode.LEN.value, DefectCode.OIL.value, DefectCode.MULTI.value
    }


# --- 파이프라인: 안 보는 항목은 계산도 안 한다 ------------------------------

def _frame():
    """밝은 막대 하나 — 길이 측정이 되는 합성 프레임."""
    f = np.full((240, 640, 3), 30, np.uint8)
    f[100:140, 120:520] = 220
    return f


def test_pipeline_length_mode_leaves_surface_scores_empty():
    vr = InspectionPipeline().run(_frame(), _item(),
                                  stage=InspectionStage.CUT_LENGTH.value)
    assert vr.surface.oil_score is None
    assert vr.surface.discolor_score is None
    assert vr.surface.scratch_score is None
    assert vr.length.meas_length_mm is not None, "길이는 재야 한다"


def test_pipeline_surface_mode_leaves_length_empty():
    vr = InspectionPipeline().run(_frame(), _item(),
                                  stage=InspectionStage.POST_WASH_SURFACE.value)
    assert vr.length.meas_length_mm is None
    assert vr.length.deviation_mm is None
    assert vr.surface.oil_score is not None, "표면은 봐야 한다"
    assert DefectCode.LEN.value not in vr.defect_codes


def test_pipeline_default_stage_field_is_used():
    pipe = InspectionPipeline(stage=InspectionStage.POST_WASH_SURFACE.value)
    vr = pipe.run(_frame(), _item())
    assert vr.length.meas_length_mm is None


def test_pipeline_call_stage_overrides_field():
    pipe = InspectionPipeline(stage=InspectionStage.POST_WASH_SURFACE.value)
    vr = pipe.run(_frame(), _item(), stage=InspectionStage.CUT_LENGTH.value)
    assert vr.length.meas_length_mm is not None
    assert vr.surface.oil_score is None


def test_pipeline_is_deterministic_per_stage():
    pipe = InspectionPipeline()
    a = pipe.run(_frame(), _item(), stage="CUT_LENGTH")
    b = pipe.run(_frame(), _item(), stage="CUT_LENGTH")
    assert a.final_verdict == b.final_verdict
    assert a.length.meas_length_mm == b.length.meas_length_mm
