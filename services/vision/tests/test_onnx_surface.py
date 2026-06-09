"""OnnxSurfaceModel 폴백 견고화 테스트 (§6.3).

모델 미배포/미설치/로드실패 어떤 경우에도 결정적 SurfaceResult 를 반환해야
한다(자동검사율 100%, 미판정 0). 실제 ONNX 추론은 모델 배포 후이므로 여기서는
폴백 경로와 경로 해석/임계 출처만 검증한다.
"""
from __future__ import annotations

from aivis_types import SurfaceResult

from vision.surface import OnnxSurfaceModel, resolve_model_path
from vision.surface.classical import analyze_surface
from vision.tools.gen_synthetic import make_image


def test_resolve_path_none_when_absent(monkeypatch):
    monkeypatch.delenv("AIVIS_SURFACE_ONNX", raising=False)
    # 기본 경로에 모델이 없으면 None.
    assert resolve_model_path() is None
    # 존재하지 않는 명시 경로도 None.
    assert resolve_model_path("/no/such/model.onnx") is None


def test_resolve_path_env(monkeypatch, tmp_path):
    f = tmp_path / "surface.onnx"
    f.write_bytes(b"not-a-real-onnx")
    monkeypatch.setenv("AIVIS_SURFACE_ONNX", str(f))
    assert resolve_model_path() == str(f)


def test_model_absent_falls_back(monkeypatch, item):
    monkeypatch.delenv("AIVIS_SURFACE_ONNX", raising=False)
    model = OnnxSurfaceModel()
    assert not model.loaded
    img, _ = make_image("OIL")
    res = model.predict(img, item)
    assert isinstance(res, SurfaceResult)
    # 폴백은 고전 CV 와 동일 결과(결정적).
    ref = analyze_surface(img, item)
    assert res.oil_score == ref.oil_score
    assert res.scratch_score == ref.scratch_score


def test_corrupt_model_falls_back(monkeypatch, tmp_path, item):
    """손상된 .onnx 가 있어도 로드 실패를 삼키고 고전 CV 폴백."""
    f = tmp_path / "surface.onnx"
    f.write_bytes(b"garbage-not-onnx")
    monkeypatch.setenv("AIVIS_SURFACE_ONNX", str(f))
    model = OnnxSurfaceModel()
    assert not model.loaded  # 로드 실패 → 폴백.
    assert model._load_error is not None
    img, _ = make_image("OK")
    res = model.predict(img, item)
    assert isinstance(res, SurfaceResult)


def test_predict_deterministic(item):
    model = OnnxSurfaceModel()
    img, _ = make_image("SCR")
    a = model.predict(img, item)
    b = model.predict(img.copy(), item)
    assert a.oil_score == b.oil_score
    assert a.scratch_score == b.scratch_score
    assert list(a.defect_codes) == list(b.defect_codes)
