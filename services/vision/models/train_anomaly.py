"""비지도 이상탐지 학습 CLI (정상 이미지만 학습) — CLAUDE.md §6.3, M16.

정상(OK) 이미지 디렉터리에서 표면 패치 기술자(anomaly.patch_features)를 모아
정상 분포의 평균 벡터 + 정칙화(λI) 공분산의 역행렬 + 학습 임계(정상 **이미지**
점수의 상위 백분위 또는 mean+kσ)를 산출해 npz 로 저장한다.

기본값(새 모델): v2 기술자 · 격자 8 · 반 패치 겹침 · 이미지 점수 = 상위 2 패치
평균(anomaly.DEFAULT_*). 근거는 DAGM 벤치마크(models/benchmarks/DAGM_RESULT.md).
예전 방식이 필요하면 `--feature-set v1 --grid 1`(겹침·top-k 는 v1 에서 무시).

임계는 **이미지(튜브 crop) 단위**로 잡는다. 패치가 P 개인 이미지를 최악 패치로
채점하면서 임계를 패치 거리의 99% 로 잡으면 정상 이미지의 1−0.99^P 가 임계를
넘는다(격자 8 = 64 패치 → 정상품 약 47% 재확인. DAGM 실측 FPR 38~40%).

- torch/onnxruntime 의존 없음(numpy/opencv 만). CPU 만. 결정적(무작위 금지).
- 학습 임계·정칙화계수는 인자에서 온다(하드코딩 금지).
- 배포: services/vision/models/anomaly_<item>.npz 로 저장하면 auto 모드에서
  AnomalySurfaceModel 이 자동 인식한다(resolve_anomaly_model_path).

사용 예:
    python -m services.vision.models.train_anomaly \
        --ok-dir dataset/raw/OK --item HP12 \
        --out services/vision/models/anomaly_HP12.npz \
        [--segment] [--percentile 99] [--k 4] [--reg 0.1] \
        [--feature-set v2] [--grid 8] [--no-overlap] [--topk 2]

--segment: 한 프레임에 튜브가 여러 개면 segment_tubes 로 분할해 튜브별 크롭에서
           학습(단면/다발 대신 개별 표면 학습).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence

import cv2
import numpy as np

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.multi.segment import segment_tubes  # noqa: E402
from vision.preprocess import preprocess  # noqa: E402
from vision.surface.anomaly import (  # noqa: E402
    DEFAULT_FEATURE_SET,
    DEFAULT_PATCH_GRID,
    DEFAULT_PATCH_OVERLAP,
    DEFAULT_SCORE_TOPK,
    FEATURE_SET_V1,
    FEATURE_SETS,
    feature_dim,
    image_score,
    patch_features,
)

_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")


def _region_and_mask(img_bgr: np.ndarray):
    """파이프라인과 동일한 표면 ROI/마스크 추출(학습·추론 일치)."""
    pre = preprocess(img_bgr)
    if pre.surface_roi is not None:
        region = pre.surface_roi.crop(img_bgr)
        rmask = pre.surface_roi.crop(pre.mask)
    else:
        region = img_bgr
        rmask = pre.mask
    return region, rmask


def _descriptor_for(
    img_bgr: np.ndarray,
    *,
    segment: bool,
    grid: int = DEFAULT_PATCH_GRID,
    feature_set: str = DEFAULT_FEATURE_SET,
    overlap: bool = DEFAULT_PATCH_OVERLAP,
) -> List[List[np.ndarray]]:
    """이미지 1장 → crop 별 기술자 리스트(segment 면 튜브별, 패치마다 1행).

    격자·기술자 세트·겹침은 추론과 반드시 같아야 한다(모델 파일에 실어 보낸다).
    학습은 전체 한 벡터로 하고 추론만 패치로 하면 비교 대상이 달라져 거리가 전부
    커진다(정상품 대량 오검). crop 단위로 묶어 돌려주는 이유는 임계를 추론과 같은
    단위(튜브 crop 1개 = 이미지 점수 1개)로 잡기 위해서다.
    """
    out: List[List[np.ndarray]] = []
    if segment:
        tubes = segment_tubes(img_bgr)
        crops = [t.crop(img_bgr) for t in tubes] if tubes else [img_bgr]
    else:
        crops = [img_bgr]
    for crop in crops:
        if crop is None or crop.size == 0 or crop.ndim != 3:
            continue
        region, rmask = _region_and_mask(crop)
        feats = patch_features(
            region, rmask, feature_set=feature_set, grid=grid, overlap=overlap
        )
        # 전경 없음(0 벡터)은 학습에서 제외.
        vecs = [vec for vec in feats if np.any(vec)]
        if vecs:
            out.append(vecs)
    return out


def list_ok_images(ok_dir: str | Path) -> List[Path]:
    """OK 이미지 파일 목록(결정적 정렬)."""
    root = Path(ok_dir)
    files = [
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
    ]
    return sorted(files)


def collect_descriptors(
    image_paths: Sequence[Path],
    *,
    segment: bool = False,
    grid: int = DEFAULT_PATCH_GRID,
    feature_set: str = DEFAULT_FEATURE_SET,
    overlap: bool = DEFAULT_PATCH_OVERLAP,
    return_groups: bool = False,
):
    """이미지 경로들 → 기술자 행렬(N x 차원). 결정적.

    return_groups=True 면 (행렬, 그룹) 을 돌려준다. 그룹은 행마다 어느 crop
    (이미지 또는 튜브)에서 나왔는지의 번호(0..m-1) — 이미지 단위 임계용.
    """
    rows: List[np.ndarray] = []
    groups: List[int] = []
    gid = 0
    for p in image_paths:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            continue
        for vecs in _descriptor_for(
            img, segment=segment, grid=grid, feature_set=feature_set, overlap=overlap
        ):
            rows.extend(vecs)
            groups.extend([gid] * len(vecs))
            gid += 1
    if not rows:
        X = np.empty((0, feature_dim(feature_set)), dtype=np.float64)
        g = np.empty(0, dtype=np.int64)
    else:
        X = np.vstack(rows).astype(np.float64)
        g = np.asarray(groups, dtype=np.int64)
    return (X, g) if return_groups else X


def _fit_gaussian(X: np.ndarray, *, reg: float) -> tuple[np.ndarray, np.ndarray]:
    """정상 분포 적합 → (mean, precision). 저표본·고차원 대비 수축 정칙화.

    1) 특징 표준화 z=(x-μ)/σ (σ 바닥값 eps) — 스케일 차이 제거.
    2) 표준화 공분산을 항등행렬로 수축(shrinkage): C_reg=(1-reg)·C+reg·I (Ledoit-Wolf 형태).
    3) 원 스케일 정밀도 P = Dσ⁻¹·C_reg⁻¹·Dσ⁻¹ 로 접어두면 추론은 표준화 없이
       d²=(x-μ)ᵀP(x-μ) 로 동일 계산된다.
    """
    n, d = X.shape
    mean = X.mean(axis=0)
    std_floor = np.maximum(X.std(axis=0), 1e-6)
    z = (X - mean) / std_floor
    cov = (
        np.atleast_2d(np.cov(z, rowvar=False))
        if n >= 2
        else np.zeros((d, d), dtype=np.float64)
    )
    cov_reg = (1.0 - reg) * cov + reg * np.eye(d)
    d_inv = np.diag(1.0 / std_floor)
    return mean, d_inv @ np.linalg.inv(cov_reg) @ d_inv


def _mahalanobis(X: np.ndarray, mean: np.ndarray, precision: np.ndarray) -> np.ndarray:
    """행별 Mahalanobis 거리(음수 클리핑 후 sqrt)."""
    diffs = X - mean
    d2 = np.einsum("ij,jk,ik->i", diffs, precision, diffs)
    return np.sqrt(np.clip(d2, 0.0, None))


def _oof_distances(
    X: np.ndarray, *, reg: float, folds: int
) -> Optional[np.ndarray]:
    """K-fold **표본외(out-of-fold)** Mahalanobis 거리. 불가하면 None.

    왜 필요한가: 같은 데이터로 공분산을 적합하고 그 데이터의 거리를 재면
    거리가 체계적으로 과소평가된다(표본내 편향). 표본수 n 이 특징차원 d 에
    비해 크지 않을 때(n/d ≲ 3) 특히 심해서, 표본내 백분위로 임계를 잡으면
    **정상 제품 상당수가 임계를 넘어 전부 재확인 대상이 된다**(실측: n=30,
    d=19 에서 홀드아웃 정상의 53% 가 임계 초과). 각 폴드를 제외하고 적합한
    뒤 그 폴드의 거리를 재면 표본외 거리 분포를 편향 없이 추정할 수 있다.

    폴드 수 보정(중요): 폴드가 적으면 폴드별 학습표본이 특징차원 d 보다 적어져
    공분산이 붕괴하고 거리가 폭발한다(실측: n=30,d=19,k=2 → 임계 44만 →
    score≈0 으로 **이상탐지가 조용히 무력화**). 폴드를 늘릴수록 폴드별 학습표본이
    늘어나므로(LOO 가 최대), 학습표본이 d+2 이상이 되는 최소 k 로 올려 잡는다.
    LOO(k=n)로도 못 맞추면(n < d+3) None 을 돌려 표본내 폴백 + 경고로 넘긴다.

    결정적: 폴드 분할은 인덱스 스트라이드(idx[f::k])로 무작위성이 없다.
    """
    n, d = X.shape
    if n < 4:
        return None  # 폴드를 나눌 만큼의 표본이 없다 → 호출자가 폴백.
    need = d + 2  # 폴드별 학습표본 하한(수축 정칙화 전제, 최소 여유 2).
    k = int(min(max(2, folds), n))
    while k <= n and (n - int(np.ceil(n / k))) < need:
        k += 1
    if k > n or (n - int(np.ceil(n / k))) < need:
        return None  # LOO 로도 부족 → 표본내 폴백(호출자가 경고).
    idx = np.arange(n)
    dists = np.empty(n, dtype=np.float64)
    for f in range(k):
        te = idx[f::k]
        tr = np.setdiff1d(idx, te)
        if tr.size < 2 or te.size == 0:
            return None
        mean_f, prec_f = _fit_gaussian(X[tr], reg=reg)
        dists[te] = _mahalanobis(X[te], mean_f, prec_f)
    return dists


def _oof_group_scores(
    X: np.ndarray, groups: np.ndarray, *, reg: float, folds: int, topk: int
) -> Optional[np.ndarray]:
    """**그룹(이미지) 단위** K-fold 표본외 이미지 점수. 불가하면 None.

    _oof_distances 와 같은 이유(표본내 거리는 과소평가)에 더해 두 가지를 맞춘다.
    1. 같은 이미지의 패치는 같은 폴드에 둔다. 이웃 패치는 서로 닮아서, 한 이미지의
       패치가 학습·평가 양쪽에 걸치면 평가가 표본내처럼 낙관적이 된다.
    2. 추론과 같은 방식(image_score, 상위 k 패치 평균)으로 이미지 점수를 낸 뒤
       그 분포에서 임계를 잡는다 — 패치 거리 백분위로 잡으면 패치가 많을수록
       정상 이미지가 임계를 넘는 비율이 1−p^P 로 커진다.

    폴드 보정은 _oof_distances 와 같다(폴드별 학습 행이 d+2 이상이 되는 최소 k).
    결정적: 폴드 분할은 그룹 번호 스트라이드(g[f::k]).
    """
    n, d = X.shape
    uniq = np.unique(groups)
    m = int(uniq.size)
    if m < 4:
        return None
    pos = np.searchsorted(uniq, groups)
    sizes = np.bincount(pos, minlength=m)
    need = d + 2
    k = int(min(max(2, folds), m))

    def min_train(kk: int) -> int:
        return int(min(n - sizes[f::kk].sum() for f in range(kk)))

    while k <= m and min_train(k) < need:
        k += 1
    if k > m:
        return None
    scores = np.empty(m, dtype=np.float64)
    for f in range(k):
        te_g = np.arange(m)[f::k]
        te = np.isin(pos, te_g)
        if te.all() or not te.any():
            return None
        mean_f, prec_f = _fit_gaussian(X[~te], reg=reg)
        dist = _mahalanobis(X[te], mean_f, prec_f)
        pos_te = pos[te]
        for j in te_g:
            scores[j] = image_score(dist[pos_te == j], topk)
    return scores


def _group_scores(
    X: np.ndarray, groups: np.ndarray, mean: np.ndarray, precision: np.ndarray,
    topk: int,
) -> np.ndarray:
    """표본내 이미지 점수(폴드를 못 나눌 때의 폴백)."""
    uniq = np.unique(groups)
    pos = np.searchsorted(uniq, groups)
    dist = _mahalanobis(X, mean, precision)
    return np.array([image_score(dist[pos == j], topk) for j in range(uniq.size)])


def fit_model(
    descriptors: np.ndarray,
    *,
    percentile: float = 99.0,
    k: Optional[float] = None,
    reg: float = 0.1,
    margin: float = 1.0,
    folds: int = 5,
    groups: Optional[np.ndarray] = None,
    topk: int = 1,
) -> dict:
    """정상 분포 적합. mean/cov_inv(정밀도)/threshold 산출(결정적).

    추론용 mean/precision 은 **전체 표본**으로 적합한다(정보 최대 활용).
    임계는 **표본외(out-of-fold) 분포**에서 잡는다 — 표본내 거리는
    과소평가되어 정상품을 대량 오검(전수 재확인)하게 만들기 때문이다
    (_oof_distances 주석의 실측 참조). 표본이 적어 폴드를 못 나누면
    표본내로 폴백하고 `threshold_basis="in_sample"` 로 표시한다.

    groups(행별 이미지 번호)를 주면 임계를 **이미지 점수**(상위 topk 패치 평균)
    분포에서 잡는다(`threshold_level="image"`, _oof_group_scores). 안 주면 예전
    그대로 행(패치) 거리 분포(`"patch"`). 이미지당 1행(격자 1)이면 둘은 같다.

    임계: k 지정 시 mean+kσ, 아니면 분포의 percentile. margin 은 추가
    여유 배율(기본 1.0). 모두 하드코딩 아닌 인자.
    """
    X = np.asarray(descriptors, dtype=np.float64)
    if X.ndim != 2 or X.shape[0] < 1:
        raise ValueError("fit_model: 기술자가 비었다(정상 이미지 부족).")
    n, d = X.shape
    reg = float(min(max(reg, 1e-6), 1.0))
    mean, precision = _fit_gaussian(X, reg=reg)
    topk = max(1, int(topk))

    if groups is not None:
        g = np.asarray(groups).ravel()
        if g.shape[0] != n:
            raise ValueError(f"fit_model: groups 길이({g.shape[0]}) ≠ 행 수({n}).")
        level = "image"
        n_groups = int(np.unique(g).size)
        oof = _oof_group_scores(X, g, reg=reg, folds=folds, topk=topk)
        if oof is not None:
            dists, basis = oof, "out_of_fold"
        else:
            dists, basis = _group_scores(X, g, mean, precision, topk), "in_sample"
    else:
        level, n_groups, topk = "patch", n, 1
        oof = _oof_distances(X, reg=reg, folds=folds)
        if oof is not None:
            dists, basis = oof, "out_of_fold"
        else:
            dists, basis = _mahalanobis(X, mean, precision), "in_sample"

    if k is not None:
        base_thr = float(dists.mean() + float(k) * dists.std())
    else:
        base_thr = float(np.percentile(dists, percentile))
    thr = max(base_thr * float(margin), 1e-6)  # 임계 > 0 보장.
    return {
        "mean": mean,
        "cov_inv": precision,
        "threshold": thr,
        "feature_dim": d,
        "n_train": n,
        "n_groups": n_groups,
        "threshold_basis": basis,
        "threshold_level": level,
        "score_topk": topk,
    }


#: npz 형식 버전. 2 = feature_set/patch_overlap/score_topk/threshold_level 추가.
#: 로더는 버전이 아니라 **키 유무**로 판단한다(키가 없으면 구 모델 값).
MODEL_FORMAT_VERSION = 2


def save_model(model: dict, out_path: str | Path, *, item_code: str) -> Path:
    """npz 저장(allow_pickle 없이 로드 가능한 배열만)."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        out,
        mean=model["mean"].astype(np.float64),
        cov_inv=model["cov_inv"].astype(np.float64),
        threshold=np.float64(model["threshold"]),
        feature_dim=np.int64(model["feature_dim"]),
        n_train=np.int64(model["n_train"]),
        # 추론이 같은 방식으로 채점하도록 모델에 싣는다(불일치 시 전부 오검).
        patch_grid=np.int64(model.get("patch_grid", 1)),
        feature_set=np.array(str(model.get("feature_set", FEATURE_SET_V1))),
        patch_overlap=np.int64(1 if model.get("patch_overlap", False) else 0),
        score_topk=np.int64(model.get("score_topk", 1)),
        threshold_level=np.array(str(model.get("threshold_level", "patch"))),
        item_code=np.array(str(item_code)),
        version=np.int64(MODEL_FORMAT_VERSION),
    )
    # np.savez 는 .npz 확장자를 자동으로 붙일 수 있다.
    if out.suffix != ".npz" and out.with_suffix(".npz").exists():
        return out.with_suffix(".npz")
    return out


def train_anomaly(
    ok_dir: str | Path,
    out_path: str | Path,
    *,
    item_code: str,
    segment: bool = False,
    percentile: float = 99.0,
    k: Optional[float] = None,
    reg: float = 0.1,
    margin: float = 1.0,
    folds: int = 5,
    grid: int = DEFAULT_PATCH_GRID,
    feature_set: str = DEFAULT_FEATURE_SET,
    overlap: bool = DEFAULT_PATCH_OVERLAP,
    topk: int = DEFAULT_SCORE_TOPK,
) -> dict:
    """정상 이미지 디렉터리 → 이상탐지 모델 학습·저장. 요약 dict 반환.

    표면을 패치로 나눠 패치별로 학습한다. 작은 국소 결함(스크래치 등)은 영역
    전체 통계에 묻히기 때문이다. 기본값(v2·격자 8·겹침·top2)과 그 근거는
    DAGM 벤치마크(vision.tools.dagm_benchmark, models/benchmarks/DAGM_RESULT.md).
    v1 에서는 겹침·top-k 를 쓰지 않는다(구 모델과 같은 채점 — 최악 패치).
    """
    if feature_set not in FEATURE_SETS:
        raise ValueError(f"알 수 없는 기술자 세트: {feature_set!r} (허용: {FEATURE_SETS})")
    if feature_set == FEATURE_SET_V1:
        overlap, topk = False, 1
    paths = list_ok_images(ok_dir)
    if not paths:
        raise FileNotFoundError(f"정상(OK) 이미지가 없다: {ok_dir}")
    X, groups = collect_descriptors(
        paths, segment=segment, grid=grid, feature_set=feature_set,
        overlap=overlap, return_groups=True,
    )
    if X.shape[0] < 1:
        raise ValueError(
            "학습 기술자를 하나도 얻지 못했다(전처리에서 전경 미검출)."
        )
    model = fit_model(
        X, percentile=percentile, k=k, reg=reg, margin=margin, folds=folds,
        groups=groups, topk=topk,
    )
    model["patch_grid"] = int(max(1, grid))
    model["feature_set"] = feature_set
    model["patch_overlap"] = bool(overlap)
    saved = save_model(model, out_path, item_code=item_code)
    n_samples, dim = int(X.shape[0]), int(model["feature_dim"])
    n_groups = int(model["n_groups"])
    summary = {
        "item_code": item_code,
        "n_images": len(paths),
        "n_crops": n_groups,
        "n_samples": n_samples,
        "feature_dim": dim,
        "feature_set": feature_set,
        "threshold": float(model["threshold"]),
        "threshold_basis": model["threshold_basis"],
        "threshold_level": model["threshold_level"],
        "out_path": str(saved),
        "segment": segment,
        "patch_grid": int(max(1, grid)),
        "patch_overlap": bool(overlap),
        "score_topk": int(model["score_topk"]),
        "warnings": _sample_warnings(
            n_samples, dim, model["threshold_basis"],
            n_groups=n_groups, percentile=None if k is not None else percentile,
        ),
    }
    return summary


# 표본수가 특징차원에 비해 이 배수 미만이면 적합이 불안정하다(경고).
MIN_SAMPLES_PER_DIM = 3.0


#: 이미지 단위 임계의 백분위 p 를 믿을 만하게 추정하려면 꼬리(1−p)에 이미지가
#: 몇 장은 들어가야 한다. 그보다 적으면 임계가 가장 튀는 정상 한두 장에 좌우된다.
MIN_TAIL_IMAGES = 2.0


def _sample_warnings(
    n_samples: int,
    dim: int,
    basis: str,
    *,
    n_groups: Optional[int] = None,
    percentile: Optional[float] = None,
) -> List[str]:
    """학습 표본 부족/임계 산출 근거에 대한 현장 경고(한국어)."""
    warns: List[str] = []
    if n_groups is not None and percentile is not None and percentile < 100.0:
        need = int(np.ceil(MIN_TAIL_IMAGES / max(1e-9, 1.0 - percentile / 100.0)))
        if n_groups < need:
            warns.append(
                f"정상 이미지(튜브) {n_groups}개는 이미지 단위 임계(상위 "
                f"{100.0 - percentile:g}%)를 잡기에 부족하다(권장 {need}개 이상) "
                "— 임계가 가장 튀는 정상 몇 장에 좌우된다. 더 모으거나 "
                "--percentile 을 낮춰라."
            )
    if basis == "in_sample":
        warns.append(
            "표본이 적어 교차검증 임계를 못 쓰고 표본내 거리로 임계를 잡았다 "
            "— 정상품이 과다하게 재확인으로 걸릴 수 있다. 정상 이미지를 더 모아라."
        )
    if n_samples < MIN_SAMPLES_PER_DIM * dim:
        warns.append(
            f"학습 표본 {n_samples}개는 특징차원 {dim}에 비해 부족하다"
            f"(권장 {int(MIN_SAMPLES_PER_DIM * dim)}개 이상). 정상 분포 추정이 "
            "불안정해 오검이 늘 수 있다."
        )
    return warns


def _main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="AIVIS 비지도 이상탐지 학습(정상 이미지만) — §6.3"
    )
    ap.add_argument("--ok-dir", required=True, help="정상(OK) 이미지 폴더")
    ap.add_argument(
        "--grid",
        type=int,
        default=DEFAULT_PATCH_GRID,
        help=(
            "패치 격자(v2: 짧은 변을 grid 칸, 긴 변은 정사각형에 가깝게 / "
            f"v1: grid x grid). 기본 {DEFAULT_PATCH_GRID}"
        ),
    )
    ap.add_argument(
        "--feature-set",
        choices=FEATURE_SETS,
        default=DEFAULT_FEATURE_SET,
        help=f"기술자 세트(기본 {DEFAULT_FEATURE_SET}; v1 = 예전 19개 통계)",
    )
    ap.add_argument(
        "--no-overlap",
        action="store_true",
        help="패치를 겹치지 않는다(기본은 반 패치 겹침, v2 전용)",
    )
    ap.add_argument(
        "--topk",
        type=int,
        default=DEFAULT_SCORE_TOPK,
        help=f"이미지 점수 = 상위 k 패치 거리 평균(기본 {DEFAULT_SCORE_TOPK}, v2 전용)",
    )
    ap.add_argument("--item", required=True, help="품목 코드(예: HP12)")
    ap.add_argument("--out", help="출력 npz(기본: models/anomaly_<item>.npz)")
    ap.add_argument(
        "--segment", action="store_true", help="다중 튜브면 튜브별로 분할해 학습"
    )
    ap.add_argument(
        "--percentile", type=float, default=99.0,
        help="임계 백분위(기본 99). --k 지정 시 무시.",
    )
    ap.add_argument(
        "--k", type=float, default=None,
        help="지정 시 임계 = mean + k·σ (백분위 대신).",
    )
    ap.add_argument(
        "--reg", type=float, default=0.1,
        help="공분산 수축(shrinkage) 정칙화 강도 reg∈(0,1] (기본 0.1)",
    )
    ap.add_argument(
        "--margin", type=float, default=1.0,
        help="임계 여유 배율(표본외 정상 퍼짐 대비, 기본 1.0)",
    )
    ap.add_argument(
        "--folds", type=int, default=5,
        help="임계 산출용 교차검증 폴드 수(기본 5). 표본이 적으면 자동 축소/폴백.",
    )
    args = ap.parse_args(argv)

    out = args.out or str(
        _SERVICES_DIR / "vision" / "models" / f"anomaly_{args.item}.npz"
    )
    summary = train_anomaly(
        args.ok_dir,
        out,
        item_code=args.item,
        segment=args.segment,
        percentile=args.percentile,
        k=args.k,
        reg=args.reg,
        margin=args.margin,
        folds=args.folds,
        grid=args.grid,
        feature_set=args.feature_set,
        overlap=not args.no_overlap,
        topk=args.topk,
    )
    basis_ko = (
        "교차검증(표본외)"
        if summary["threshold_basis"] == "out_of_fold"
        else "표본내(표본 부족)"
    )
    overlap_ko = "예" if summary["patch_overlap"] else "아니오"
    print(
        f"[이상탐지 학습] 품목={summary['item_code']} "
        f"이미지={summary['n_images']}장(crop {summary['n_crops']}개) "
        f"학습표본={summary['n_samples']}개 "
        f"기술자={summary['feature_set']}({summary['feature_dim']}차원) "
        f"격자={summary['patch_grid']} 겹침={overlap_ko} "
        f"top{summary['score_topk']} "
        f"임계(Mahalanobis)={summary['threshold']:.4f} "
        f"임계근거={basis_ko}"
    )
    print(f"  저장: {summary['out_path']} (segment={summary['segment']})")
    for w in summary["warnings"]:
        print(f"  [경고] {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
