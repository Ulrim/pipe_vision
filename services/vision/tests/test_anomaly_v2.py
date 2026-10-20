"""이상탐지 v2 기술자·이미지 단위 임계·구 모델 하위호환 (§6.3 고도화).

근거: DAGM 2007 Class1 벤치마크(vision/models/benchmarks/DAGM_RESULT.md).
v1(통계 19개, 정사각 격자, 최악 패치, 패치 거리 임계) → v2(질감 에너지 15개 +
로그, 짧은 변 기준 패치, 반 패치 겹침, 상위 2 패치 평균, 이미지 점수 임계).

여기서 지키는 계약:
1. 예전에 학습한 npz(키 없음 = v1)는 예전과 **똑같이** 채점한다.
2. 새로 학습하는 모델의 기본값은 v2 · 격자 8 · 겹침 · top2 이다.
3. 임계는 추론과 같은 단위(이미지 점수)에서 잡는다 — 패치가 많아도 정상
   이미지가 대량으로 재확인에 걸리지 않는다.
4. 가늘고 긴 튜브 crop 에서도 패치가 실제로 나뉜다(v1 은 1개로 떨어졌다).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from vision.models.train_anomaly import (
    _mahalanobis,
    fit_model,
    save_model,
    train_anomaly,
)
from vision.surface.anomaly import (
    DEFAULT_FEATURE_SET,
    DEFAULT_PATCH_GRID,
    DEFAULT_PATCH_OVERLAP,
    DEFAULT_SCORE_TOPK,
    FEATURE_DIM,
    FEATURE_DIM_V2,
    FEATURE_NAMES_V2,
    AnomalySurfaceModel,
    feature_dim,
    image_score,
    mahalanobis_distance,
    mahalanobis_rows,
    patch_descriptors,
    patch_features,
    patch_features_v2,
    patch_layout,
)
from vision.tools.gen_synthetic import make_image

_BENCH_DIR = Path(__file__).resolve().parents[1] / "models" / "benchmarks"
#: 구 형식(v1·격자 8·feature_set 키 없음) 실물 모델 — 하위호환 고정물.
_OLD_MODEL = _BENCH_DIR / "dagm_class1_grid8.npz"


def _texture(seed: int = 0, h: int = 256, w: int = 256) -> np.ndarray:
    """결정적 의사 텍스처(살짝 흐린 노이즈). 결함 없는 '정상' 표면.

    정상끼리도 질감 세기(대비)와 밝기가 장마다 조금씩 다르게 준다(조명·로트 편차).
    이 편차가 없으면 영역 전체 한 벡터로도 무엇이든 잡혀 패치의 효과를 볼 수 없다.
    """
    rng = np.random.default_rng(seed)
    contrast = 0.8 + 0.4 * rng.random()
    offset = rng.integers(-15, 16)
    noise = rng.normal(0.0, 22.0 * contrast, size=(h, w))
    g = np.clip(130 + offset + noise, 0, 255).astype(np.uint8)
    g = cv2.GaussianBlur(g, (0, 0), 1.2)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def _with_smudge(img: np.ndarray, *, r: int = 14) -> np.ndarray:
    """작은 국소 결함 — 질감이 뭉개진 얼룩(DAGM Class1 결함과 비슷한 성질).

    밝기 경계가 없어 영역 전체 통계에는 거의 티가 나지 않는다(면적 ~1%).
    """
    out = img.copy()
    h, w = out.shape[:2]
    blur = cv2.GaussianBlur(img, (0, 0), 3.0)
    m = np.zeros((h, w), np.uint8)
    cv2.circle(m, (w // 3, h // 3), r, 255, thickness=-1)
    out[m > 0] = blur[m > 0]
    return out


# --- 기본값 -------------------------------------------------------------------


def test_new_model_defaults() -> None:
    assert DEFAULT_FEATURE_SET == "v2"
    assert DEFAULT_PATCH_GRID == 8
    assert DEFAULT_PATCH_OVERLAP is True
    assert DEFAULT_SCORE_TOPK == 2
    assert FEATURE_DIM_V2 == len(FEATURE_NAMES_V2) == 15
    assert feature_dim("v1") == FEATURE_DIM and feature_dim("v2") == FEATURE_DIM_V2
    with pytest.raises(ValueError):
        feature_dim("v9")


def test_train_defaults_are_recorded_in_model(tmp_path: Path) -> None:
    """학습 기본값이 모델 파일에 실려 추론이 그대로 따라간다."""
    ok = tmp_path / "ok"
    ok.mkdir()
    for i in range(10):
        img, _ = make_image("OK", seed=i)
        cv2.imwrite(str(ok / f"HP12_SIDE_OK_{i:03d}.png"), img)
    summary = train_anomaly(ok, tmp_path / "m.npz", item_code="HP12", folds=3)
    assert summary["feature_set"] == "v2"
    assert summary["patch_grid"] == DEFAULT_PATCH_GRID
    assert summary["patch_overlap"] is True
    assert summary["score_topk"] == DEFAULT_SCORE_TOPK
    m = AnomalySurfaceModel(summary["out_path"], item_code="HP12")
    assert m.loaded
    assert (m._feature_set, m._grid, m._overlap, m._topk) == ("v2", 8, True, 2)


# --- 패치 배치 -----------------------------------------------------------------


@pytest.mark.parametrize(
    "h,w,grid,expected",
    [
        (512, 512, 8, (8, 8)),      # 정사각형: v1 과 같은 g x g
        (512, 512, 16, (16, 16)),
        (64, 1728, 8, (2, 54)),     # 다발 튜브 crop: 짧은 변 2칸(32px), 긴 변 정사각형
        (96, 1728, 8, (3, 54)),
        (1728, 64, 8, (54, 2)),     # 세로 crop 도 대칭
        (48, 1728, 8, (1, 36)),     # 짧은 변 < 64px → 1행, 48px 정사각형
        (96, 1728, 16, (3, 54)),    # 짧은 변이 상한이면 격자 8과 16이 같다
        (512, 512, 1, (1, 1)),
    ],
)
def test_patch_layout(h: int, w: int, grid: int, expected: tuple) -> None:
    assert patch_layout(h, w, grid) == expected


def test_v1_square_grid_collapses_on_thin_tube_but_v2_does_not() -> None:
    """v1 의 grid x grid 는 가는 튜브 crop 에서 짧은 변 32px 제한에 걸려 1칸이 된다.

    다발(튜브 12~20개) 한 개의 표면 crop 이 대략 1728x48~64 다. v1 에서는 격자를
    얼마로 학습해도 표면 전체를 한 덩어리로 봤다 — 작은 스크래치에 눈이 없었다.
    """
    tube = _texture(h=48, w=1728)
    assert patch_descriptors(tube, None, grid=8).shape[0] == 1
    assert patch_features_v2(tube, None, grid=8, overlap=False).shape[0] == 36
    assert patch_features_v2(tube, None, grid=8, overlap=True).shape[0] == 71


# --- v2 기술자 -----------------------------------------------------------------


def test_v2_shapes_and_determinism() -> None:
    img = _texture(seed=3)
    a = patch_features_v2(img, None, grid=8, overlap=True)
    b = patch_features_v2(img.copy(), None, grid=8, overlap=True)
    assert a.shape == (15 * 15, FEATURE_DIM_V2)   # 겹침: (2*8-1)^2
    assert np.array_equal(a, b)
    assert np.isfinite(a).all()
    assert patch_features_v2(img, None, grid=8, overlap=False).shape == (64, FEATURE_DIM_V2)
    # 디스패처도 같은 값.
    c = patch_features(img, None, feature_set="v2", grid=8, overlap=True)
    assert np.array_equal(a, c)
    with pytest.raises(ValueError):
        patch_features(img, None, feature_set="v9")


def test_v2_grayscale_color_features_are_constant() -> None:
    """흑백 입력에서는 색(a/b) 평균이 일정·편차 0 — 거리에 기여하지 않는다(DAGM)."""
    f = patch_features_v2(_texture(seed=1), None)
    ab_mean = f[:, 11:13]
    ab_std = f[:, 13:15]
    assert np.allclose(ab_mean, ab_mean[0])
    assert np.allclose(ab_std, 0.0)


def test_v2_mask_drops_background_patches() -> None:
    img = _texture(seed=2)
    full = patch_features_v2(img, None, grid=8, overlap=False)
    mask = np.full(img.shape[:2], 255, np.uint8)
    mask[:, :96] = 0  # 왼쪽 3칸(패치 폭 32px) 배경
    part = patch_features_v2(img, mask, grid=8, overlap=False)
    assert part.shape[0] == 8 * 5
    # 전부 전경인 마스크는 마스크 없음과 같다.
    assert np.array_equal(
        patch_features_v2(img, np.full(img.shape[:2], 255, np.uint8)), patch_features_v2(img)
    )
    assert full.shape[0] == 64


@pytest.mark.parametrize("bad", [None, np.zeros((0, 0, 3), dtype=np.uint8)])
def test_v2_safe_on_empty(bad) -> None:
    out = patch_features_v2(bad, None)
    assert out.shape == (1, FEATURE_DIM_V2) and not np.any(out)


def test_v2_zero_mask_returns_zero_row() -> None:
    img = _texture()
    out = patch_features_v2(img, np.zeros(img.shape[:2], np.uint8))
    assert out.shape == (1, FEATURE_DIM_V2) and not np.any(out)


def test_image_score_topk() -> None:
    d = np.array([1.0, 5.0, 3.0, 4.0])
    assert image_score(d, 1) == 5.0
    assert image_score(d, 2) == 4.5
    assert image_score(d, 10) == pytest.approx(13.0 / 4)
    assert image_score(np.array([]), 2) == 0.0


def test_v2_local_defect_needs_patches() -> None:
    """질감이 뭉개진 작은 얼룩은 패치로 나눠야 잡힌다(v2 에서도 패치 도입의 근거).

    영역 전체 한 벡터(격자 1)로는 임계 아래, 격자 8 에서는 임계를 크게 넘는다.
    정상 홀드아웃은 둘 다 임계 아래 — 임계가 느슨해서 잡힌 것이 아니다.
    """
    normals = [_texture(seed=i) for i in range(16)]
    defect = _with_smudge(_texture(seed=999))
    holds = [_texture(seed=1000 + i) for i in range(6)]

    def ratios(grid: int) -> tuple[float, float]:
        feats = [patch_features_v2(n, None, grid=grid) for n in normals]
        groups = np.concatenate([np.full(f.shape[0], j) for j, f in enumerate(feats)])
        m = fit_model(np.vstack(feats), groups=groups, topk=2)

        def score(img: np.ndarray) -> float:
            f = patch_features_v2(img, None, grid=grid)
            return image_score(_mahalanobis(f, m["mean"], m["cov_inv"]), 2) / m["threshold"]

        return score(defect), max(score(h) for h in holds)

    d1, n1 = ratios(1)
    d8, n8 = ratios(8)
    assert d1 < 1.0, f"격자 1 에서 이미 잡혔다({d1:.2f}) — 시험 결함이 너무 크다"
    assert d8 > 1.5, f"격자 8 에서도 못 잡았다({d8:.2f})"
    assert n8 < 1.0


# --- 이미지 단위 임계 ------------------------------------------------------------


def test_image_threshold_equals_patch_threshold_with_one_row_per_image() -> None:
    """이미지당 1행이면 이미지 단위 임계 = 예전 패치 단위 임계(같은 폴드·같은 값)."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(40, 6))
    a = fit_model(X)
    b = fit_model(X, groups=np.arange(40), topk=1)
    assert a["threshold"] == pytest.approx(b["threshold"], rel=1e-12)
    assert (a["threshold_level"], b["threshold_level"]) == ("patch", "image")


def test_image_threshold_prevents_mass_false_alarms() -> None:
    """패치가 많은데 임계를 패치 거리 99% 로 잡으면 정상 이미지 상당수가 걸린다.

    이미지마다 패치 P 개 중 최악을 쓰므로 정상 이미지가 임계를 넘을 확률이
    1−0.99^P 다(DAGM 격자 8 = 64 패치 → 실측 오검률 38~40%). 이미지 점수 분포에서
    임계를 잡으면 이 문제가 사라진다.
    """
    train = [patch_features_v2(_texture(seed=i), None) for i in range(24)]
    groups = np.concatenate([np.full(f.shape[0], j) for j, f in enumerate(train)])
    X = np.vstack(train)
    patch_m = fit_model(X)
    image_m = fit_model(X, groups=groups, topk=2)
    hold = [patch_features_v2(_texture(seed=500 + i), None) for i in range(12)]

    def flagged(m: dict) -> int:
        return sum(
            image_score(_mahalanobis(f, m["mean"], m["cov_inv"]), 2) >= m["threshold"]
            for f in hold
        )

    assert flagged(patch_m) >= 6, "패치 단위 임계가 정상을 대량으로 걸러야 회귀 재현"
    assert flagged(image_m) <= 1


def test_fit_model_groups_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        fit_model(np.zeros((10, 3)) + np.arange(3), groups=np.arange(9))


# --- 하위호환: 구 형식 npz ---------------------------------------------------------


def test_committed_old_model_loads_as_v1() -> None:
    """저장소에 있는 구 형식 모델(v1·격자8, feature_set 키 없음)이 예전대로 로드된다."""
    assert _OLD_MODEL.exists()
    data = np.load(_OLD_MODEL, allow_pickle=False)
    assert "feature_set" not in data.files and int(data["version"]) == 1
    m = AnomalySurfaceModel(str(_OLD_MODEL))
    assert m.loaded, m._load_error
    assert (m._feature_set, m._grid, m._overlap, m._topk) == ("v1", 8, False, 1)


def test_old_model_scores_exactly_like_before(item) -> None:
    """구 모델 채점 = 예전 코드(v1 패치 → 행마다 mahalanobis_distance → 최댓값)."""
    m = AnomalySurfaceModel(str(_OLD_MODEL))
    img = _texture(seed=7, h=512, w=512)
    m.predict(img, item)
    feats = patch_descriptors(img, None, grid=8)
    data = np.load(_OLD_MODEL, allow_pickle=False)
    expected = max(
        mahalanobis_distance(v, data["mean"], data["cov_inv"]) for v in feats
    )
    assert m.last_report is not None and m.last_report.loaded
    assert m.last_report.distance == round(expected, 6)


def test_legacy_npz_without_any_new_keys(tmp_path: Path, item) -> None:
    """patch_grid 조차 없는 가장 오래된 형식 → v1 · 격자 1 · 최악 패치."""
    X = np.vstack([patch_descriptors(_texture(seed=i), None, grid=1) for i in range(20)])
    model = fit_model(X, reg=0.1)
    out = tmp_path / "oldest.npz"
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
    m = AnomalySurfaceModel(str(out))
    assert m.loaded
    assert (m._feature_set, m._grid, m._overlap, m._topk) == ("v1", 1, False, 1)
    m.predict(_texture(seed=50), item)
    v = patch_descriptors(_texture(seed=50), None, grid=1)[0]
    assert m.last_report.distance == round(
        mahalanobis_distance(v, model["mean"], model["cov_inv"]), 6
    )


# --- v2 모델 왕복 -----------------------------------------------------------------


def _v2_model(tmp_path: Path, *, topk: int = 2) -> Path:
    feats = [patch_features_v2(_texture(seed=i), None) for i in range(20)]
    groups = np.concatenate([np.full(f.shape[0], j) for j, f in enumerate(feats)])
    m = fit_model(np.vstack(feats), groups=groups, topk=topk)
    m.update(patch_grid=8, feature_set="v2", patch_overlap=True)
    return save_model(m, tmp_path / "v2.npz", item_code="T")


def test_v2_model_roundtrip_and_topk_scoring(tmp_path: Path, item) -> None:
    path = _v2_model(tmp_path)
    data = np.load(path, allow_pickle=False)
    assert str(data["feature_set"]) == "v2" and int(data["score_topk"]) == 2
    assert str(data["threshold_level"]) == "image" and int(data["patch_overlap"]) == 1
    m = AnomalySurfaceModel(str(path))
    assert m.loaded and (m._feature_set, m._grid, m._overlap, m._topk) == ("v2", 8, True, 2)

    img = _with_smudge(_texture(seed=77))
    m.predict(img, item)
    rep1 = m.last_report
    m.predict(img.copy(), item)
    assert m.last_report == rep1  # 결정적
    f = patch_features_v2(img, None, grid=8, overlap=True)
    expected = image_score(mahalanobis_rows(f, data["mean"], data["cov_inv"]), 2)
    assert rep1.distance == round(expected, 6)
    assert rep1.review_flag is True  # 얼룩은 정상 분포 밖


def test_v2_model_with_wrong_dimension_is_rejected(tmp_path: Path, item) -> None:
    """v2 라고 적혀 있는데 배열이 19차원이면 로드하지 않고 고전 CV 로 폴백."""
    X = np.vstack([patch_descriptors(_texture(seed=i), None, grid=1) for i in range(20)])
    m = fit_model(X)
    m["feature_set"] = "v2"
    path = save_model(m, tmp_path / "bad.npz", item_code="T")
    model = AnomalySurfaceModel(str(path))
    assert not model.loaded and "차원" in (model._load_error or "")
    model.predict(_texture(), item)
    assert model.last_report is not None and model.last_report.loaded is False


def test_unknown_feature_set_is_rejected(tmp_path: Path) -> None:
    path = _v2_model(tmp_path)
    data = dict(np.load(path, allow_pickle=False))
    data["feature_set"] = np.array("v9")
    bad = tmp_path / "v9.npz"
    np.savez(bad, **data)
    model = AnomalySurfaceModel(str(bad))
    assert not model.loaded and "v9" in (model._load_error or "")
