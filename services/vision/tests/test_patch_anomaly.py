"""패치 단위 이상탐지 (§6.3 고도화).

**왜 패치로 나누는가**: 기술자는 영역 전체의 평균·표준편차·분위수다. 표면이 넓으면
작은 국소 결함(스크래치가 대표적)이 그 통계를 거의 흔들지 못해 정상과 구분되지
않는다. DAGM 2007 공개 벤치마크에서 실측한 내용이다 — 같은 기술자·같은 모델로
전체를 한 벡터로 보면 AUROC 0.885, 8x8 패치로 나눠 최악 패치로 채점하면 1.000
(vision/models/benchmarks/DAGM_RESULT.md).

여기서 지키는 계약:
1. grid=1 은 기존 동작과 완전히 같다(구 모델 하위호환).
2. 학습과 추론의 격자가 같아야 한다 — 모델 파일이 격자를 싣고 다닌다.
3. 작은 국소 결함이 전체 채점보다 패치 채점에서 더 크게 잡힌다.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from vision.models.train_anomaly import fit_model, save_model, train_anomaly
from vision.surface.anomaly import (
    FEATURE_DIM,
    AnomalySurfaceModel,
    extract_descriptor,
    patch_descriptors,
)
from vision.tools.gen_synthetic import make_image


def _texture(seed: int = 0, size: int = 256) -> np.ndarray:
    """결정적 의사 텍스처(균일 노이즈). 결함 없는 '정상' 표면."""
    rng = np.random.default_rng(seed)
    g = rng.integers(110, 140, size=(size, size), dtype=np.int16).astype(np.uint8)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def _with_spot(img: np.ndarray, *, box: int = 14) -> np.ndarray:
    """작은 국소 결함(어두운 점) 주입 — 면적이 전체의 0.3% 수준."""
    out = img.copy()
    h, w = out.shape[:2]
    y, x = h // 3, w // 3
    out[y : y + box, x : x + box] = 20
    return out


def test_grid1_matches_whole_region_descriptor() -> None:
    """grid=1 은 기존 전체 기술자와 동일해야 한다(하위호환)."""
    img = _texture()
    whole = extract_descriptor(img)
    patched = patch_descriptors(img, None, grid=1)
    assert patched.shape == (1, FEATURE_DIM)
    assert np.allclose(patched[0], whole)


def test_grid_produces_expected_patch_count() -> None:
    img = _texture(size=256)
    assert patch_descriptors(img, None, grid=4).shape == (16, FEATURE_DIM)
    assert patch_descriptors(img, None, grid=8).shape == (64, FEATURE_DIM)


def test_grid_downgraded_when_patches_too_small() -> None:
    """패치가 너무 작으면 통계가 불안정하므로 격자를 자동으로 낮춘다."""
    small = _texture(size=64)          # 64/32 = 최대 2x2
    out = patch_descriptors(small, None, grid=16)
    assert out.shape[0] <= 4, f"과도하게 잘게 쪼갰다: {out.shape}"


def test_local_defect_stands_out_more_with_patches() -> None:
    """작은 국소 결함은 전체 채점보다 패치 채점에서 훨씬 크게 드러난다.

    이 테스트가 패치 도입의 근거 그 자체다. 깨지면 패치로 나눌 이유가 없어진다.
    """
    normals = [_texture(seed=i) for i in range(24)]
    defect = _with_spot(_texture(seed=99))

    def ratio(grid: int) -> float:
        X = np.vstack([patch_descriptors(n, None, grid=grid) for n in normals])
        m = fit_model(X, percentile=99.0, reg=0.1)
        from vision.models.train_anomaly import _mahalanobis

        d_def = _mahalanobis(
            patch_descriptors(defect, None, grid=grid), m["mean"], m["cov_inv"]
        ).max()
        d_ok = _mahalanobis(
            patch_descriptors(_texture(seed=101), None, grid=grid),
            m["mean"],
            m["cov_inv"],
        ).max()
        return float(d_def / max(d_ok, 1e-9))

    assert ratio(8) > ratio(1), "패치로 나눠도 국소 결함 대비가 나아지지 않았다"


def test_model_carries_grid_and_infers_with_it(tmp_path: Path) -> None:
    """모델 파일이 격자를 싣고, 추론이 그 격자로 채점한다.

    학습은 패치로 하고 추론은 전체로 하면(또는 반대) 비교 대상이 달라져 거리가
    통째로 어긋난다 — 정상품을 대량 오검하게 된다.
    """
    X = np.vstack([patch_descriptors(_texture(seed=i), None, grid=4) for i in range(20)])
    model = fit_model(X, percentile=99.0, reg=0.1)
    model["patch_grid"] = 4
    out = save_model(model, tmp_path / "m.npz", item_code="T")

    loaded = AnomalySurfaceModel(str(out), item_code="T")
    assert loaded.loaded
    assert loaded._grid == 4


def test_legacy_model_without_grid_defaults_to_1(tmp_path: Path) -> None:
    """격자 키가 없는 예전 npz 도 그대로 로드돼 기존 동작을 유지한다."""
    X = np.vstack([extract_descriptor(_texture(seed=i)) for i in range(20)])
    model = fit_model(X, percentile=99.0, reg=0.1)
    out = tmp_path / "legacy.npz"
    np.savez(
        out,
        mean=model["mean"],
        cov_inv=model["cov_inv"],
        threshold=np.float64(model["threshold"]),
        feature_dim=np.int64(model["feature_dim"]),
        n_train=np.int64(model["n_train"]),
        item_code=np.array("T"),
        version=np.int64(1),
    )
    loaded = AnomalySurfaceModel(str(out), item_code="T")
    assert loaded.loaded
    assert loaded._grid == 1


def test_train_cli_records_grid(tmp_path: Path) -> None:
    """학습 CLI 가 격자를 모델에 기록한다(추론이 같은 격자를 쓰게)."""
    ok = tmp_path / "ok"
    ok.mkdir()
    for i in range(12):
        img, _ = make_image("OK", seed=i)
        cv2.imwrite(str(ok / f"HP12_SIDE_OK_20260610-14{i:04d}_{i:03d}.jpg"), img)

    summary = train_anomaly(
        ok, tmp_path / "m.npz", item_code="HP12", grid=2, folds=3
    )
    assert summary["patch_grid"] == 2
    loaded = AnomalySurfaceModel(summary["out_path"], item_code="HP12")
    assert loaded.loaded and loaded._grid == 2


@pytest.mark.parametrize("bad", [None, np.zeros((0, 0, 3), dtype=np.uint8)])
def test_patch_descriptors_safe_on_empty(bad) -> None:
    """빈 입력에도 예외 없이 0 벡터 1행(검사 루프가 멈추면 안 된다)."""
    out = patch_descriptors(bad, None, grid=4)
    assert out.shape == (1, FEATURE_DIM)


# --- 처리속도 실측 도구 (§1.2 300ms/ea) --------------------------------------


def test_bench_reports_each_grid_and_recommends() -> None:
    """격자별 시간을 재고 예산에 맞는 가장 큰 격자를 고른다."""
    from vision.tools.bench_anomaly import bench, recommend

    rows = bench(_texture(size=128), grids=(1, 2, 4), repeat=2)
    assert [r["grid"] for r in rows] == [1, 2, 4]
    assert all(r["ms_median"] > 0 for r in rows)
    assert all(r["patches"] >= 1 for r in rows)

    # 예산이 넉넉하면 가장 큰 격자, 빠듯하면 작은 격자, 불가능하면 None.
    assert recommend(rows, budget_ms=10_000.0) == 4
    assert recommend(rows, budget_ms=0.0) is None


def test_bench_recommendation_is_within_budget() -> None:
    """권장 격자는 반드시 예산 안이어야 한다(넘으면 현장에서 검사가 밀린다)."""
    from vision.tools.bench_anomaly import bench, recommend

    rows = bench(_texture(size=192), grids=(1, 2, 4), repeat=2)
    budget = max(r["ms_median"] for r in rows) * 0.6
    best = recommend(rows, budget_ms=budget)
    if best is not None:
        chosen = next(r for r in rows if r["grid"] == best)
        assert chosen["ms_median"] <= budget
