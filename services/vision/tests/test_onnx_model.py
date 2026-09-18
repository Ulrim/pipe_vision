"""ONNX 표면 모델 결선 테스트 — GitHub 공개 모델 적용 경로 (§6.3).

onnx.helper 로 **실제 미니 ONNX 모델**을 만들어 검증한다(모킹 없음):
1) kind=surface3: 사이드카 계약 → 점수/ItemMaster 임계 판정.
2) 사이드카 없음 + 출력명 oil/discolor/scratch → 자동 인식(구 골격 관례).
3) 사이드카 없음 + 모르는 출력명 → loaded=False + classical 회귀.
4) 사이드카 손상 → 폴백 + _load_error.
5) 실행 중 shape 오류 → 예외 전파 없이 classical 폴백.
6) kind=anomaly: map 최대값 점수화, threshold/review 채널, force_ng.
7) resolve_surface_model 우선순위(onnx>anomaly>classical, env 강제 모드).
8) check_onnx CLI 스모크.
모두 결정적, AIVIS_CAMERA=sim 전제(순수 CPU/ONNX Runtime).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from aivis_types import DefectCode, SurfaceResult, Verdict

from vision.surface import (
    AnomalySurfaceModel,
    ClassicalSurfaceModel,
    OnnxSurfaceModel,
    analyze_surface,
    resolve_surface_model,
)
from vision.surface.onnx_meta import OnnxMetaError, load_onnx_meta
from vision.tools.gen_synthetic import make_image

onnx = pytest.importorskip("onnx", reason="onnx 패키지(모델 생성용) 필요")
from onnx import TensorProto, helper  # noqa: E402


# ---------------------------------------------------------------------------
# 미니 ONNX 모델 빌더(입력을 실제로 소비하되 고정 출력 — 결정적 검증)
# ---------------------------------------------------------------------------
def _finalize(graph) -> "onnx.ModelProto":
    model = helper.make_model(
        graph, opset_imports=[helper.make_opsetid("", 17)]
    )
    model.ir_version = 8  # 구 onnxruntime 호환.
    onnx.checker.check_model(model)
    return model


def _make_surface3_model(
    path: Path, h: int = 16, w: int = 16, scores=(0.9, 0.1, 0.7)
) -> None:
    """[1,3,H,W] 입력을 소비(GAP→0곱)하고 고정 [1,3] 점수를 내는 모델."""
    inp = helper.make_tensor_value_info(
        "input", TensorProto.FLOAT, [1, 3, h, w]
    )
    out = helper.make_tensor_value_info("scores", TensorProto.FLOAT, [1, 3])
    zeros = helper.make_tensor("zeros", TensorProto.FLOAT, [1, 3], [0.0] * 3)
    bias = helper.make_tensor("bias", TensorProto.FLOAT, [1, 3], list(scores))
    nodes = [
        helper.make_node("GlobalAveragePool", ["input"], ["gap"]),
        helper.make_node("Flatten", ["gap"], ["flat"], axis=1),
        helper.make_node("Mul", ["flat", "zeros"], ["zeroed"]),
        helper.make_node("Add", ["zeroed", "bias"], ["scores"]),
    ]
    graph = helper.make_graph(
        nodes, "surface3_mini", [inp], [out], initializer=[zeros, bias]
    )
    onnx.save(_finalize(graph), str(path))


def _make_named_outputs_model(
    path: Path, h: int = 16, w: int = 16, scores=(0.9, 0.1, 0.7)
) -> None:
    """출력 이름이 정확히 oil/discolor/scratch 인 모델(구 골격 관례)."""
    inp = helper.make_tensor_value_info(
        "input", TensorProto.FLOAT, [1, 3, h, w]
    )
    outs = [
        helper.make_tensor_value_info(n, TensorProto.FLOAT, [1, 1])
        for n in ("oil", "discolor", "scratch")
    ]
    zeros = helper.make_tensor("zeros", TensorProto.FLOAT, [1, 3], [0.0] * 3)
    inits = [zeros]
    nodes = [
        helper.make_node("GlobalAveragePool", ["input"], ["gap"]),
        helper.make_node("Flatten", ["gap"], ["flat"], axis=1),
        helper.make_node("Mul", ["flat", "zeros"], ["zeroed"]),
        helper.make_node(
            "ReduceSum", ["zeroed"], ["zsum"], keepdims=1
        ),  # [1,1] 0값
    ]
    for name, val in zip(("oil", "discolor", "scratch"), scores):
        b = helper.make_tensor(f"b_{name}", TensorProto.FLOAT, [1, 1], [val])
        inits.append(b)
        nodes.append(helper.make_node("Add", ["zsum", f"b_{name}"], [name]))
    graph = helper.make_graph(
        nodes, "surface3_named", [inp], outs, initializer=inits
    )
    onnx.save(_finalize(graph), str(path))


def _make_anomaly_model(
    path: Path, h: int = 8, w: int = 8, max_val: float = 0.8
) -> None:
    """[1,3,H,W] 입력 소비 → 고정 anomaly map [1,1,4,4] (최댓값=max_val)."""
    inp = helper.make_tensor_value_info(
        "input", TensorProto.FLOAT, [1, 3, h, w]
    )
    out = helper.make_tensor_value_info(
        "anomaly_map", TensorProto.FLOAT, [1, 1, 4, 4]
    )
    wgt = helper.make_tensor("W", TensorProto.FLOAT, [3, 16], [0.0] * 48)
    map_vals = list(np.linspace(0.0, max_val, 16, dtype=np.float64))
    bias = helper.make_tensor("B", TensorProto.FLOAT, [16], map_vals)
    shape = helper.make_tensor("S", TensorProto.INT64, [4], [1, 1, 4, 4])
    nodes = [
        helper.make_node("GlobalAveragePool", ["input"], ["gap"]),
        helper.make_node("Flatten", ["gap"], ["flat"], axis=1),
        helper.make_node("MatMul", ["flat", "W"], ["mm"]),
        helper.make_node("Add", ["mm", "B"], ["biased"]),
        helper.make_node("Reshape", ["biased", "S"], ["anomaly_map"]),
    ]
    graph = helper.make_graph(
        nodes, "anomaly_mini", [inp], [out], initializer=[wgt, bias, shape]
    )
    onnx.save(_finalize(graph), str(path))


def _write_sidecar(model_path: Path, payload: dict) -> Path:
    sc = model_path.with_suffix(".json")
    sc.write_text(json.dumps(payload), encoding="utf-8")
    return sc


SURFACE3_SIDECAR = {
    "kind": "surface3",
    "input_size": [16, 16],
    "outputs": {"oil": 0, "discolor": 1, "scratch": 2},
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in (
        "AIVIS_SURFACE_ONNX",
        "AIVIS_SURFACE_MODEL",
        "AIVIS_SURFACE_ANOMALY",
        "AIVIS_SURFACE_ANOMALY_MODEL",
        "AIVIS_ONNX_PROVIDERS",
    ):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------
# 1) surface3 — 사이드카 계약 + ItemMaster 임계 판정
# ---------------------------------------------------------------------------
def test_surface3_scores_and_item_thresholds(tmp_path, item):
    f = tmp_path / "surface.onnx"
    _make_surface3_model(f, scores=(0.9, 0.1, 0.7))
    _write_sidecar(f, SURFACE3_SIDECAR)
    model = OnnxSurfaceModel(str(f))
    assert model.loaded, model._load_error
    img, _ = make_image("OK")
    res = model.predict(img, item)
    assert isinstance(res, SurfaceResult)
    assert res.oil_score == pytest.approx(0.9, abs=1e-4)
    assert res.discolor_score == pytest.approx(0.1, abs=1e-4)
    assert res.scratch_score == pytest.approx(0.7, abs=1e-4)
    # item 임계(oil .30 / dis .20 / scr .15) → OIL, SCR 만 초과.
    assert list(res.defect_codes) == [DefectCode.OIL, DefectCode.SCR]
    assert res.surface_verdict == Verdict.NG.value
    assert res.proc_time_ms >= 0
    # 결정성.
    res2 = model.predict(img.copy(), item)
    assert res2.oil_score == res.oil_score
    assert list(res2.defect_codes) == list(res.defect_codes)


def test_surface3_threshold_from_item_master(tmp_path, item):
    """임계 하드코딩 금지 — 같은 점수라도 item 임계에 따라 판정이 바뀐다."""
    f = tmp_path / "surface.onnx"
    _make_surface3_model(f, scores=(0.4, 0.1, 0.1))
    _write_sidecar(f, SURFACE3_SIDECAR)
    model = OnnxSurfaceModel(str(f))
    img, _ = make_image("OK")
    # oil 0.4 >= 0.30 → NG(OIL).
    ng = model.predict(img, item)
    assert list(ng.defect_codes) == [DefectCode.OIL]
    # 임계 완화(0.5) → OK.
    loose = item.model_copy(update={"oil_threshold": 0.5})
    ok = model.predict(img, loose)
    assert ok.defect_codes == []
    assert ok.surface_verdict == Verdict.OK.value


# ---------------------------------------------------------------------------
# 2) 사이드카 없음 — 구 관례 자동 인식 / 모르는 출력은 명시적 실패
# ---------------------------------------------------------------------------
def test_named_outputs_auto_detected_without_sidecar(tmp_path, item):
    f = tmp_path / "legacy.onnx"
    _make_named_outputs_model(f, scores=(0.9, 0.1, 0.7))
    model = OnnxSurfaceModel(str(f))
    assert model.loaded, model._load_error
    assert model.meta is not None
    assert model.meta.kind == "surface3"
    assert model.meta.input_size == (16, 16)
    img, _ = make_image("OK")
    res = model.predict(img, item)
    assert res.oil_score == pytest.approx(0.9, abs=1e-4)
    assert res.scratch_score == pytest.approx(0.7, abs=1e-4)


def test_no_sidecar_unknown_outputs_fails_loud_falls_back(tmp_path, item):
    f = tmp_path / "unknown.onnx"
    _make_surface3_model(f)  # 출력명 "scores" — 관례 불일치, 사이드카 없음.
    model = OnnxSurfaceModel(str(f))
    assert not model.loaded
    assert model._load_error is not None and "사이드카" in model._load_error
    img, _ = make_image("OIL")
    res = model.predict(img, item)
    ref = analyze_surface(img, item)
    assert res.oil_score == ref.oil_score
    assert list(res.defect_codes) == list(ref.defect_codes)


# ---------------------------------------------------------------------------
# 3) 사이드카 손상/검증 오류 → 폴백 + _load_error
# ---------------------------------------------------------------------------
def test_corrupt_sidecar_falls_back(tmp_path, item):
    f = tmp_path / "surface.onnx"
    _make_surface3_model(f)
    (tmp_path / "surface.json").write_text("{broken json", encoding="utf-8")
    model = OnnxSurfaceModel(str(f))
    assert not model.loaded
    assert model._load_error is not None
    img, _ = make_image("OK")
    res = model.predict(img, item)
    assert isinstance(res, SurfaceResult)


def test_sidecar_unknown_kind_rejected(tmp_path):
    f = tmp_path / "surface.onnx"
    _make_surface3_model(f)
    sc = _write_sidecar(f, {"kind": "yolo-seg", "input_size": [16, 16]})
    with pytest.raises(OnnxMetaError):
        load_onnx_meta(sc)
    model = OnnxSurfaceModel(str(f))
    assert not model.loaded
    assert "kind" in (model._load_error or "")


# ---------------------------------------------------------------------------
# 4) 실행 중 shape 오류 → 예외 전파 없이 classical 폴백
# ---------------------------------------------------------------------------
def test_runtime_shape_mismatch_falls_back(tmp_path, item):
    f = tmp_path / "surface.onnx"
    _make_surface3_model(f, h=16, w=16)
    sidecar = dict(SURFACE3_SIDECAR)
    sidecar["input_size"] = [32, 32]  # 모델 기대(16)와 불일치 → run 실패.
    _write_sidecar(f, sidecar)
    model = OnnxSurfaceModel(str(f))
    assert model.loaded  # 로드는 성공, 실행에서 실패한다.
    img, _ = make_image("SCR")
    res = model.predict(img, item)  # raise 하면 안 된다.
    ref = analyze_surface(img, item)
    assert res.scratch_score == ref.scratch_score
    assert list(res.defect_codes) == list(ref.defect_codes)
    # 반복 호출에도 안전(경고 1회 정책 내부 상태 확인).
    model.predict(img, item)
    assert model._runtime_warned is True


# ---------------------------------------------------------------------------
# 5) anomaly — map 최대값 점수화 + review 채널 + force_ng
# ---------------------------------------------------------------------------
def _anomaly_sidecar(threshold: float, force_ng: bool = False) -> dict:
    return {
        "kind": "anomaly",
        "input_size": [8, 8],
        "anomaly": {"threshold": threshold, "force_ng": force_ng},
    }


def test_anomaly_map_review_and_classical_preserved(tmp_path, item):
    f = tmp_path / "anomaly.onnx"
    _make_anomaly_model(f, max_val=0.8)
    _write_sidecar(f, _anomaly_sidecar(threshold=0.5))
    model = OnnxSurfaceModel(str(f))
    assert model.loaded, model._load_error
    img, _ = make_image("OK")
    res = model.predict(img, item)
    ref = analyze_surface(img, item)
    # classical named 점수/코드/verdict 보존.
    assert res.oil_score == ref.oil_score
    assert res.discolor_score == ref.discolor_score
    assert res.scratch_score == ref.scratch_score
    assert list(res.defect_codes) == list(ref.defect_codes)
    assert res.surface_verdict == ref.surface_verdict
    # 이상점수 0.8 >= 임계 0.5 → review 채널만 ON(NG 강제 X).
    rep = model.last_report
    assert rep is not None and rep.loaded
    assert rep.distance == pytest.approx(0.8, abs=1e-4)
    assert rep.review_flag is True
    assert rep.score == 1.0  # min(1, 0.8/0.5)


def test_anomaly_below_threshold_no_review(tmp_path, item):
    f = tmp_path / "anomaly.onnx"
    _make_anomaly_model(f, max_val=0.3)
    _write_sidecar(f, _anomaly_sidecar(threshold=0.5))
    model = OnnxSurfaceModel(str(f))
    img, _ = make_image("OK")
    model.predict(img, item)
    rep = model.last_report
    assert rep is not None and rep.review_flag is False
    assert rep.score == pytest.approx(0.6, abs=1e-3)  # 0.3/0.5


def test_anomaly_force_ng_adds_multi(tmp_path, item):
    f = tmp_path / "anomaly.onnx"
    _make_anomaly_model(f, max_val=0.9)
    _write_sidecar(f, _anomaly_sidecar(threshold=0.5, force_ng=True))
    model = OnnxSurfaceModel(str(f))
    img, _ = make_image("OK")
    res = model.predict(img, item)
    assert DefectCode.MULTI in list(res.defect_codes)
    assert res.surface_verdict == Verdict.NG.value


def test_anomaly_deterministic(tmp_path, item):
    f = tmp_path / "anomaly.onnx"
    _make_anomaly_model(f, max_val=0.8)
    _write_sidecar(f, _anomaly_sidecar(threshold=0.5))
    model = OnnxSurfaceModel(str(f))
    img, _ = make_image("MULTI")
    a = model.predict(img, item)
    rep_a = model.last_report
    b = model.predict(img.copy(), item)
    rep_b = model.last_report
    assert a.oil_score == b.oil_score
    assert rep_a.distance == rep_b.distance
    assert rep_a.review_flag == rep_b.review_flag


# ---------------------------------------------------------------------------
# 6) resolve_surface_model 우선순위 — onnx > anomaly > classical
# ---------------------------------------------------------------------------
def test_resolve_auto_prefers_loaded_onnx(tmp_path, monkeypatch, item):
    f = tmp_path / "surface.onnx"
    _make_surface3_model(f)
    _write_sidecar(f, SURFACE3_SIDECAR)
    monkeypatch.setenv("AIVIS_SURFACE_ONNX", str(f))
    # 이상탐지 npz env 가 있어도 로드 성공한 ONNX 가 우선.
    npz = tmp_path / "anomaly_HP12.npz"
    npz.write_bytes(b"whatever")
    monkeypatch.setenv("AIVIS_SURFACE_ANOMALY_MODEL", str(npz))
    model = resolve_surface_model(item)
    assert isinstance(model, OnnxSurfaceModel)
    assert model.loaded


def test_resolve_auto_onnx_load_fail_falls_to_anomaly(
    tmp_path, monkeypatch, item
):
    f = tmp_path / "surface.onnx"
    _make_surface3_model(f)  # 사이드카 없음 + 출력명 불일치 → 로드 실패.
    monkeypatch.setenv("AIVIS_SURFACE_ONNX", str(f))
    npz = tmp_path / "anomaly_HP12.npz"
    npz.write_bytes(b"whatever")  # 존재만 하면 AnomalySurfaceModel 선택.
    monkeypatch.setenv("AIVIS_SURFACE_ANOMALY_MODEL", str(npz))
    model = resolve_surface_model(item)
    assert isinstance(model, AnomalySurfaceModel)


def test_resolve_no_models_is_classical(item):
    assert isinstance(resolve_surface_model(item), ClassicalSurfaceModel)


def test_resolve_forced_modes(tmp_path, monkeypatch, item):
    f = tmp_path / "surface.onnx"
    _make_surface3_model(f)
    _write_sidecar(f, SURFACE3_SIDECAR)
    monkeypatch.setenv("AIVIS_SURFACE_ONNX", str(f))
    monkeypatch.setenv("AIVIS_SURFACE_MODEL", "classical")
    assert isinstance(resolve_surface_model(item), ClassicalSurfaceModel)
    monkeypatch.setenv("AIVIS_SURFACE_MODEL", "anomaly")
    assert isinstance(resolve_surface_model(item), AnomalySurfaceModel)
    monkeypatch.setenv("AIVIS_SURFACE_MODEL", "onnx")
    m = resolve_surface_model(item)
    assert isinstance(m, OnnxSurfaceModel) and m.loaded


def test_resolve_forced_onnx_without_model_still_safe(monkeypatch, item):
    monkeypatch.setenv("AIVIS_SURFACE_MODEL", "onnx")
    m = resolve_surface_model(item)
    assert isinstance(m, OnnxSurfaceModel)
    assert not m.loaded  # 내부 classical 폴백으로 미판정 0 유지.
    img, _ = make_image("OK")
    assert isinstance(m.predict(img, item), SurfaceResult)


def test_resolve_anomaly_env_compat_off(monkeypatch, item):
    """기존 AIVIS_SURFACE_ANOMALY=off 동작 회귀 없음(ONNX 부재 시)."""
    monkeypatch.setenv("AIVIS_SURFACE_ANOMALY", "off")
    assert isinstance(resolve_surface_model(item), ClassicalSurfaceModel)


# ---------------------------------------------------------------------------
# 7) check_onnx CLI 스모크
# ---------------------------------------------------------------------------
def test_check_onnx_cli_smoke(tmp_path, capsys):
    from vision.tools.check_onnx import main

    f = tmp_path / "surface.onnx"
    _make_surface3_model(f)
    _write_sidecar(f, SURFACE3_SIDECAR)
    rc = main(["--model", str(f), "--repeats", "3"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "합격" in out


def test_check_onnx_cli_load_fail(tmp_path):
    from vision.tools.check_onnx import main

    f = tmp_path / "broken.onnx"
    f.write_bytes(b"not-onnx")
    assert main(["--model", str(f), "--repeats", "1"]) == 1
