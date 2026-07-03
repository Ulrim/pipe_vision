"""파이프라인 안정화 테스트 — 어떤 입력/예외에도 미판정 0(자동검사율 100%).

취득 후 단계(전처리/길이/표면/종합)에서 예외가 나도 결정적 NG 를 반환하고,
run_safe() 는 오류 사유를 별도 채널로 돌려준다(shared-types 스키마 불변).
"""
from __future__ import annotations

import numpy as np
from aivis_types import Verdict

from vision.pipeline import InspectionPipeline
from vision.surface.model import SurfaceModel
from vision.tools.gen_synthetic import make_image


class _BoomSurface(SurfaceModel):
    """표면 추론이 항상 예외를 던지는 모델(장애 주입)."""

    def predict(self, surface_region_bgr, item, *, mask=None):
        raise RuntimeError("surface backend exploded")


def test_surface_failure_yields_deterministic_ng(item):
    img, _ = make_image("OK")
    pipe = InspectionPipeline(surface_model=_BoomSurface())
    v = pipe.run(img, item)
    # 표면 추론 실패에도 미판정 없이 NG.
    assert v.final_verdict == Verdict.NG.value
    assert v.review_flag is True


def test_run_safe_reports_reason(item):
    img, _ = make_image("OK")
    pipe = InspectionPipeline(surface_model=_BoomSurface())
    v, reason = pipe.run_safe(img, item)
    assert v.final_verdict == Verdict.NG.value
    assert reason is not None and "surface" in reason


def test_run_safe_clean_has_no_reason(item):
    img, _ = make_image("OK")
    v, reason = InspectionPipeline().run_safe(img, item)
    assert reason is None
    assert v.final_verdict in (Verdict.OK.value, Verdict.NG.value)


def test_garbage_frame_no_unadjudicated(item):
    """완전 무의미한 프레임(끝단/표면 모두 불명)도 판정은 반드시 난다."""
    junk = np.zeros((8, 8, 3), dtype=np.uint8)
    v, reason = InspectionPipeline().run_safe(junk, item)
    assert v.final_verdict in (Verdict.OK.value, Verdict.NG.value)
    # 어떤 경우에도 final_verdict 가 채워진다(미판정 0).
    assert v.proc_time_ms >= 0


def test_tiny_frame_does_not_crash(item):
    tiny = np.full((2, 2, 3), 127, dtype=np.uint8)
    v = InspectionPipeline().run(tiny, item)
    assert v.final_verdict in (Verdict.OK.value, Verdict.NG.value)


def test_run_never_raises_on_various_inputs(item):
    pipe = InspectionPipeline()
    for cls in ("OK", "LEN", "OIL", "DIS", "SCR", "MULTI"):
        img, _ = make_image(cls)
        v = pipe.run(img, item)
        assert v.final_verdict in (Verdict.OK.value, Verdict.NG.value)
