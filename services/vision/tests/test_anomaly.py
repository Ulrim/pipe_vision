"""비지도 이상탐지(PaDiM-lite / Mahalanobis) 테스트 — §6.3.

검증:
1) 모델 파일 없음 → AnomalySurfaceModel.predict 가 classical 과 동일(회귀 없음),
   loaded=False, last_report.review_flag=False.
2) 합성 OK 이미지들로 학습 → npz 생성 → 로드 → 정상 유사 이미지는 이상점수
   낮음/review 미설정, 명백히 다른(결함) 이미지는 이상점수 높음/review=True.
3) 학습 CLI 스모크(npz 존재 + threshold>0).
4) 파이프라인 결선: 모델 없을 때 표면 판정 회귀 없음.
5) 결정성 + proc_time_ms 반환.
모두 결정적(무작위 시드 고정), AIVIS_CAMERA=sim 불필요(순수 CV).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from aivis_types import SurfaceResult, Verdict

from vision.models.train_anomaly import fit_model, train_anomaly
from vision.pipeline import InspectionPipeline
from vision.preprocess import preprocess
from vision.surface import (
    FEATURE_DIM,
    AnomalySurfaceModel,
    ClassicalSurfaceModel,
    analyze_surface,
    extract_descriptor,
    resolve_anomaly_model_path,
)
from vision.tools.gen_synthetic import make_image


# --- 헬퍼: 파이프라인과 동일한 표면 ROI/마스크 ---
def _region_mask(img):
    pre = preprocess(img)
    if pre.surface_roi is not None:
        return pre.surface_roi.crop(img), pre.surface_roi.crop(pre.mask)
    return img, pre.mask


def _ok_variant(i: int, std: int = 3) -> np.ndarray:
    """결정적 노이즈를 준 OK 변형(학습 분포에 변동성 부여)."""
    img, _ = make_image("OK")
    rng = np.random.default_rng(1000 + i)
    noise = rng.integers(-std, std + 1, size=img.shape, dtype=np.int16)
    return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def _write_ok_dataset(dirpath: Path, n: int = 30) -> Path:
    # 무손실 PNG: 학습(파일)과 추론(원본 프레임) 기술자 분포 일치(JPEG 압축
    # 아티팩트로 인한 학습/추론 불일치 방지 — 합성 테스트 결정성 확보).
    okdir = dirpath / "OK"
    okdir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        img = _ok_variant(i)
        cv2.imwrite(str(okdir / f"HP12_SIDE_OK_2026-{i:03d}.png"), img)
    return okdir


# ---------------------------------------------------------------------------
# 1) 모델 없음 → classical 폴백(회귀 없음)
# ---------------------------------------------------------------------------
def test_resolve_anomaly_path_none_when_absent(monkeypatch):
    monkeypatch.delenv("AIVIS_SURFACE_ANOMALY_MODEL", raising=False)
    assert resolve_anomaly_model_path("HP12") is None
    assert resolve_anomaly_model_path("HP12", "/no/such.npz") is None
    assert resolve_anomaly_model_path(None) is None


def test_resolve_anomaly_path_env(monkeypatch, tmp_path):
    f = tmp_path / "anomaly_HP12.npz"
    f.write_bytes(b"not-a-real-npz")
    monkeypatch.setenv("AIVIS_SURFACE_ANOMALY_MODEL", str(f))
    assert resolve_anomaly_model_path("HP12") == str(f)


def test_model_absent_falls_back(monkeypatch, item):
    monkeypatch.delenv("AIVIS_SURFACE_ANOMALY_MODEL", raising=False)
    model = AnomalySurfaceModel(item_code="HP12")
    assert not model.loaded
    img, _ = make_image("OIL")
    region, mask = _region_mask(img)
    res = model.predict(region, item, mask=mask)
    assert isinstance(res, SurfaceResult)
    ref = analyze_surface(region, item, mask=mask)
    assert res.oil_score == ref.oil_score
    assert res.discolor_score == ref.discolor_score
    assert res.scratch_score == ref.scratch_score
    assert list(res.defect_codes) == list(ref.defect_codes)
    # 미로드 → review 부가정보 없음.
    assert model.last_report is not None
    assert model.last_report.loaded is False
    assert model.last_report.review_flag is False


def test_corrupt_model_falls_back(monkeypatch, tmp_path, item):
    f = tmp_path / "anomaly_HP12.npz"
    f.write_bytes(b"garbage-not-npz")
    monkeypatch.setenv("AIVIS_SURFACE_ANOMALY_MODEL", str(f))
    model = AnomalySurfaceModel(item_code="HP12")
    assert not model.loaded
    assert model._load_error is not None
    img, _ = make_image("OK")
    region, mask = _region_mask(img)
    res = model.predict(region, item, mask=mask)
    assert isinstance(res, SurfaceResult)


# ---------------------------------------------------------------------------
# 2)/3) 학습 CLI + 이상 플래깅
# ---------------------------------------------------------------------------
def test_train_creates_npz(tmp_path):
    okdir = _write_ok_dataset(tmp_path, n=24)
    out = tmp_path / "anomaly_HP12.npz"
    summary = train_anomaly(okdir, out, item_code="HP12", margin=3.0)
    assert Path(summary["out_path"]).exists()
    assert summary["threshold"] > 0.0
    assert summary["feature_dim"] == FEATURE_DIM
    assert summary["n_samples"] == 24
    # npz 내용 검증(allow_pickle 없이 로드 가능).
    data = np.load(summary["out_path"], allow_pickle=False)
    assert data["mean"].shape == (FEATURE_DIM,)
    assert data["cov_inv"].shape == (FEATURE_DIM, FEATURE_DIM)
    assert float(data["threshold"]) > 0.0
    assert int(data["feature_dim"]) == FEATURE_DIM


def test_trained_model_flags_anomaly(tmp_path, item):
    okdir = _write_ok_dataset(tmp_path, n=30)
    out = tmp_path / "anomaly_HP12.npz"
    summary = train_anomaly(okdir, out, item_code="HP12", margin=3.0)
    model = AnomalySurfaceModel(model_path=summary["out_path"], item_code="HP12")
    assert model.loaded

    # 정상 유사(학습 분포의 held-out 변형) → 이상점수 낮음, review 미설정.
    normal = _ok_variant(5000)
    region, mask = _region_mask(normal)
    model.predict(region, item, mask=mask)
    normal_rep = model.last_report
    assert normal_rep is not None and normal_rep.loaded
    assert normal_rep.review_flag is False
    assert normal_rep.score < 1.0

    # 명백히 다른(복합결함) → 이상점수 최대, review 설정.
    anomalous, _ = make_image("MULTI")
    region2, mask2 = _region_mask(anomalous)
    model.predict(region2, item, mask=mask2)
    anom_rep = model.last_report
    assert anom_rep.review_flag is True
    assert anom_rep.score >= normal_rep.score
    assert anom_rep.distance > normal_rep.distance


def test_predict_always_runs_classical(tmp_path, item):
    """이상탐지 모델이 있어도 classical named 점수/코드는 그대로 산출."""
    okdir = _write_ok_dataset(tmp_path, n=20)
    out = tmp_path / "anomaly_HP12.npz"
    summary = train_anomaly(okdir, out, item_code="HP12", margin=3.0)
    model = AnomalySurfaceModel(model_path=summary["out_path"], item_code="HP12")
    img, _ = make_image("SCR")
    region, mask = _region_mask(img)
    res = model.predict(region, item, mask=mask)
    ref = analyze_surface(region, item, mask=mask)
    # 이상탐지는 final NG 를 강제하지 않고 classical 판정을 보존한다.
    assert res.scratch_score == ref.scratch_score
    assert list(res.defect_codes) == list(ref.defect_codes)
    assert res.surface_verdict == ref.surface_verdict
    assert res.proc_time_ms >= 0


def test_predict_deterministic(tmp_path, item):
    okdir = _write_ok_dataset(tmp_path, n=20)
    out = tmp_path / "anomaly_HP12.npz"
    summary = train_anomaly(okdir, out, item_code="HP12", margin=3.0)
    model = AnomalySurfaceModel(model_path=summary["out_path"], item_code="HP12")
    img, _ = make_image("MULTI")
    region, mask = _region_mask(img)
    a = model.predict(region, item, mask=mask)
    rep_a = model.last_report
    b = model.predict(region.copy(), item, mask=mask.copy())
    rep_b = model.last_report
    assert a.oil_score == b.oil_score
    assert a.scratch_score == b.scratch_score
    assert rep_a.distance == rep_b.distance
    assert rep_a.score == rep_b.score
    assert rep_a.review_flag == rep_b.review_flag


def test_cli_smoke(tmp_path):
    from vision.models.train_anomaly import _main

    okdir = _write_ok_dataset(tmp_path, n=12)
    out = tmp_path / "anomaly_HP12.npz"
    rc = _main(
        [
            "--ok-dir",
            str(okdir),
            "--item",
            "HP12",
            "--out",
            str(out),
            "--margin",
            "3.0",
        ]
    )
    assert rc == 0
    assert out.exists()
    data = np.load(str(out), allow_pickle=False)
    assert float(data["threshold"]) > 0.0


# ---------------------------------------------------------------------------
# 특징 추출 견고성
# ---------------------------------------------------------------------------
def test_descriptor_shape_and_deterministic():
    img, _ = make_image("OK")
    region, mask = _region_mask(img)
    v1 = extract_descriptor(region, mask)
    v2 = extract_descriptor(region.copy(), mask.copy())
    assert v1.shape == (FEATURE_DIM,)
    assert np.array_equal(v1, v2)
    assert np.isfinite(v1).all()


def test_descriptor_empty_foreground_zero():
    img, _ = make_image("OK")
    zero_mask = np.zeros(img.shape[:2], dtype=np.uint8)
    v = extract_descriptor(img, zero_mask)
    assert v.shape == (FEATURE_DIM,)
    assert not np.any(v)


def test_fit_model_empty_raises():
    import pytest

    with pytest.raises(ValueError):
        fit_model(np.empty((0, FEATURE_DIM)))


# ---------------------------------------------------------------------------
# 4) 파이프라인 결선 — 모델 없을 때 회귀 없음
# ---------------------------------------------------------------------------
def test_pipeline_no_model_regression(monkeypatch, item):
    monkeypatch.delenv("AIVIS_SURFACE_ANOMALY", raising=False)
    monkeypatch.delenv("AIVIS_SURFACE_ANOMALY_MODEL", raising=False)
    classical_pipe = InspectionPipeline(surface_model=ClassicalSurfaceModel())
    auto_pipe = InspectionPipeline()  # 지연 선택(auto, 모델 없음)
    for cls in ("OK", "SCR", "OIL", "DIS", "MULTI"):
        img, _ = make_image(cls)
        ref = classical_pipe.run(img, item)
        got = auto_pipe.run(img, item)
        assert got.final_verdict == ref.final_verdict
        assert list(got.defect_codes) == list(ref.defect_codes)
        assert got.review_flag == ref.review_flag
        assert got.surface.oil_score == ref.surface.oil_score
        assert got.surface.scratch_score == ref.surface.scratch_score


def test_pipeline_mode_off_uses_classical(monkeypatch, item):
    monkeypatch.setenv("AIVIS_SURFACE_ANOMALY", "off")
    pipe = InspectionPipeline()
    model = pipe._select_surface_model(item)
    assert isinstance(model, ClassicalSurfaceModel)


def test_pipeline_uses_anomaly_when_model_present(monkeypatch, tmp_path, item):
    okdir = _write_ok_dataset(tmp_path, n=20)
    out = tmp_path / "anomaly_HP12.npz"
    train_anomaly(okdir, out, item_code="HP12", margin=3.0)
    monkeypatch.setenv("AIVIS_SURFACE_ANOMALY_MODEL", str(out))
    monkeypatch.setenv("AIVIS_SURFACE_ANOMALY", "auto")
    pipe = InspectionPipeline()
    model = pipe._select_surface_model(item)
    assert isinstance(model, AnomalySurfaceModel)
    assert model.loaded
    # 결함 이미지: 이상탐지가 review_flag 를 올린다(스키마 미변경 경로).
    img, _ = make_image("MULTI")
    result = pipe.run(img, item)
    assert result.review_flag is True
    # OK 이미지: classical OK + 이상탐지 정상 → 최종 판정 결정적 반환.
    ok_img, _ = make_image("OK")
    ok_res = pipe.run(ok_img, item)
    assert ok_res.final_verdict in (Verdict.OK.value, Verdict.NG.value)
