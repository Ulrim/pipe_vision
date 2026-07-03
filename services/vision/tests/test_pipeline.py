"""파이프라인 오케스트레이션 테스트 — 전체 <300ms, 결정성, 매핑, sim 연동."""
from __future__ import annotations


from aivis_types import DefectCode, InspectionResult, Verdict

from vision.acquisition import AcquisitionService, create_camera
from vision.pipeline import (
    InspectionPipeline,
    to_inspection_result,
)
from vision.tools.gen_synthetic import make_image


def test_pipeline_ok(item):
    img, _ = make_image("OK")
    pipe = InspectionPipeline()
    v = pipe.run(img, item)
    assert v.final_verdict == Verdict.OK.value
    assert v.defect_codes == []
    assert v.proc_time_ms >= 0


def test_pipeline_multi(item):
    img, _ = make_image("MULTI")
    v = InspectionPipeline().run(img, item)
    assert v.final_verdict == Verdict.NG.value
    assert DefectCode.MULTI.value in v.defect_codes


def test_pipeline_accepts_item_dict(item):
    img, _ = make_image("OK")
    v = InspectionPipeline().run(img, item.model_dump())
    assert v.final_verdict in (Verdict.OK.value, Verdict.NG.value)


def test_pipeline_under_300ms(item):
    img, _ = make_image("MULTI")
    pipe = InspectionPipeline()
    pipe.run(img, item)  # warmup (캐시/CLAHE 등)
    _, timings = pipe.run_with_timings(img, item)
    assert timings.total_ms < 300, f"{timings.total_ms}ms exceeds 300ms"


def test_pipeline_deterministic(item):
    img, _ = make_image("SCR")
    a = InspectionPipeline().run(img, item)
    b = InspectionPipeline().run(img.copy(), item)
    assert a.final_verdict == b.final_verdict
    assert list(a.defect_codes) == list(b.defect_codes)
    assert a.confidence == b.confidence
    assert a.length.meas_length_mm == b.length.meas_length_mm


def test_to_inspection_result_mapping(item):
    img, _ = make_image("OK")
    v = InspectionPipeline().run(img, item)
    ins = to_inspection_result(
        v, lot="L1", item_code="HP12", cam_id="CAM1"
    )
    assert isinstance(ins, InspectionResult)
    assert ins.lot == "L1"
    assert ins.final_verdict == v.final_verdict
    assert ins.meas_length_mm == v.length.meas_length_mm
    assert ins.proc_time_ms == v.proc_time_ms
    assert ins.id is None and ins.mes_synced is False


def test_end_to_end_with_simulator(monkeypatch, dataset_dir, item):
    """AIVIS_CAMERA=sim 전 경로: trigger 없이 grab→pipeline→mapping."""
    monkeypatch.setenv("AIVIS_CAMERA", "sim")
    cam = create_camera(dataset_dir=str(dataset_dir), view_filter="SIDE")
    svc = AcquisitionService(camera=cam)
    pipe = InspectionPipeline()
    g = svc.grab_with_retry()
    assert g.ok
    v = pipe.run(g.frame, item)
    ins = to_inspection_result(v, lot="L1", item_code="HP12", cam_id="CAM1")
    assert ins.final_verdict in (Verdict.OK.value, Verdict.NG.value)
