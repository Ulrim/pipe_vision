"""비지도 이상탐지 학습 CLI (정상 이미지만 학습) — CLAUDE.md §6.3, M16.

정상(OK) 이미지 디렉터리에서 표면 기술자(anomaly.extract_descriptor)를 모아
정상 분포의 평균 벡터 + 정칙화(λI) 공분산의 역행렬 + 학습 임계(정상 분포
Mahalanobis 거리의 상위 백분위 또는 mean+kσ)를 산출해 npz 로 저장한다.

- torch/onnxruntime 의존 없음(numpy/opencv 만). CPU 만. 결정적(무작위 금지).
- 학습 임계·정칙화계수는 인자에서 온다(하드코딩 금지).
- 배포: services/vision/models/anomaly_<item>.npz 로 저장하면 auto 모드에서
  AnomalySurfaceModel 이 자동 인식한다(resolve_anomaly_model_path).

사용 예:
    python -m services.vision.models.train_anomaly \
        --ok-dir dataset/raw/OK --item HP12 \
        --out services/vision/models/anomaly_HP12.npz \
        [--segment] [--percentile 99] [--k 4] [--reg 1e-3]

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
from vision.surface.anomaly import FEATURE_DIM, extract_descriptor  # noqa: E402

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


def _descriptor_for(img_bgr: np.ndarray, *, segment: bool) -> List[np.ndarray]:
    """이미지 1장 → 기술자 리스트(segment 면 튜브별 여러 개)."""
    vecs: List[np.ndarray] = []
    if segment:
        tubes = segment_tubes(img_bgr)
        crops = [t.crop(img_bgr) for t in tubes] if tubes else [img_bgr]
    else:
        crops = [img_bgr]
    for crop in crops:
        if crop is None or crop.size == 0 or crop.ndim != 3:
            continue
        region, rmask = _region_and_mask(crop)
        vec = extract_descriptor(region, rmask)
        if np.any(vec):  # 전경 없음(0 벡터)은 학습에서 제외.
            vecs.append(vec)
    return vecs


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
    image_paths: Sequence[Path], *, segment: bool = False
) -> np.ndarray:
    """이미지 경로들 → 기술자 행렬(N x FEATURE_DIM). 결정적."""
    rows: List[np.ndarray] = []
    for p in image_paths:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            continue
        rows.extend(_descriptor_for(img, segment=segment))
    if not rows:
        return np.empty((0, FEATURE_DIM), dtype=np.float64)
    return np.vstack(rows).astype(np.float64)


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


def fit_model(
    descriptors: np.ndarray,
    *,
    percentile: float = 99.0,
    k: Optional[float] = None,
    reg: float = 0.1,
    margin: float = 1.0,
    folds: int = 5,
) -> dict:
    """정상 분포 적합. mean/cov_inv(정밀도)/threshold 산출(결정적).

    추론용 mean/precision 은 **전체 표본**으로 적합한다(정보 최대 활용).
    임계는 **표본외(out-of-fold) 거리 분포**에서 잡는다 — 표본내 거리는
    과소평가되어 정상품을 대량 오검(전수 재확인)하게 만들기 때문이다
    (_oof_distances 주석의 실측 참조). 표본이 적어 폴드를 못 나누면
    표본내 거리로 폴백하고 `threshold_basis="in_sample"` 로 표시한다.

    임계: k 지정 시 mean+kσ, 아니면 거리 분포의 percentile. margin 은 추가
    여유 배율(기본 1.0). 모두 하드코딩 아닌 인자.
    """
    X = np.asarray(descriptors, dtype=np.float64)
    if X.ndim != 2 or X.shape[0] < 1:
        raise ValueError("fit_model: 기술자가 비었다(정상 이미지 부족).")
    n, d = X.shape
    reg = float(min(max(reg, 1e-6), 1.0))
    mean, precision = _fit_gaussian(X, reg=reg)

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
        "threshold_basis": basis,
    }


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
        item_code=np.array(str(item_code)),
        version=np.int64(1),
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
) -> dict:
    """정상 이미지 디렉터리 → 이상탐지 모델 학습·저장. 요약 dict 반환."""
    paths = list_ok_images(ok_dir)
    if not paths:
        raise FileNotFoundError(f"정상(OK) 이미지가 없다: {ok_dir}")
    X = collect_descriptors(paths, segment=segment)
    if X.shape[0] < 1:
        raise ValueError(
            "학습 기술자를 하나도 얻지 못했다(전처리에서 전경 미검출)."
        )
    model = fit_model(
        X, percentile=percentile, k=k, reg=reg, margin=margin, folds=folds
    )
    saved = save_model(model, out_path, item_code=item_code)
    n_samples, dim = int(X.shape[0]), int(model["feature_dim"])
    summary = {
        "item_code": item_code,
        "n_images": len(paths),
        "n_samples": n_samples,
        "feature_dim": dim,
        "threshold": float(model["threshold"]),
        "threshold_basis": model["threshold_basis"],
        "out_path": str(saved),
        "segment": segment,
        "warnings": _sample_warnings(n_samples, dim, model["threshold_basis"]),
    }
    return summary


# 표본수가 특징차원에 비해 이 배수 미만이면 적합이 불안정하다(경고).
MIN_SAMPLES_PER_DIM = 3.0


def _sample_warnings(n_samples: int, dim: int, basis: str) -> List[str]:
    """학습 표본 부족/임계 산출 근거에 대한 현장 경고(한국어)."""
    warns: List[str] = []
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
    )
    basis_ko = (
        "교차검증(표본외)"
        if summary["threshold_basis"] == "out_of_fold"
        else "표본내(표본 부족)"
    )
    print(
        f"[이상탐지 학습] 품목={summary['item_code']} "
        f"이미지={summary['n_images']}장 "
        f"학습표본={summary['n_samples']}개 "
        f"특징차원={summary['feature_dim']} "
        f"임계(Mahalanobis)={summary['threshold']:.4f} "
        f"임계근거={basis_ko}"
    )
    print(f"  저장: {summary['out_path']} (segment={summary['segment']})")
    for w in summary["warnings"]:
        print(f"  [경고] {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
