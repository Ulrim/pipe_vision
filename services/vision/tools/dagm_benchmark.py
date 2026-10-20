"""DAGM 2007 공개 벤치마크로 이상탐지 엔진 검증 (CLAUDE.md §6.3, §1.2).

**무엇을 위한 도구인가**: 지금까지 이상탐지 엔진(PaDiM-lite)은 우리가 직접 그린
합성 도형으로만 검증됐다. 출제자와 응시자가 같아 성능 수치에 의미가 없었다.
DAGM 2007 은 산업 표면결함 검출의 표준 벤치마크라, 여기서 나온 숫자는 **문헌의
다른 방법들과 직접 비교된다.** 즉 "엔진이 방법으로서 동작하는가" 를 처음으로
외부 기준에 걸어 확인할 수 있다.

**이 도구가 만드는 모델은 에이엠피 제품에 쓸 수 없다.** 두 가지 이유다.
  1. PaDiM 계열은 "정상 분포" 를 학습한다. DAGM 텍스처의 정상 분포와 알루미늄
     헤더파이프의 정상 분포는 다른 분포다. 모델 파일을 그대로 가져다 쓰면 모든
     것이 이상으로 나온다.
  2. DAGM 은 흑백이다. 색 기술자(v1 의 a/b·colorfulness, v2 의 a/b 평균·편차)는
     학습이 안 된다(분산 0 — 거리에 기여하지 않는다). 변색(DIS) 판정은 색이
     근거인데 그 축을 여기서는 검증하지 못한다.
따라서 여기서 얻는 것은 **모델이 아니라 방법의 타당성과 하이퍼파라미터**다
(기술자 구성, 격자, 겹침, 이미지 점수 집계, 임계 산출 방식). 제품 모델은 에이엠피
정상품 사진으로 다시 학습해야 한다.

**평가 두 가지**(둘 다 결함은 학습에 쓰지 않는다 — 비지도의 전제):
  A. 공식 분할: Train 정상으로 학습 → Test 정상+결함 평가.
  B. 교차검증: 정상 전부(Train+Test)를 K 폴드로 나눠, 폴드마다 나머지 정상으로
     학습하고 그 폴드 정상 + 결함 전부를 평가. AUROC 는 폴드 평균±표준편차,
     재현율·정밀도·오검률은 폴드 합산. 분할 하나에 운이 좋은 설정을 걸러낸다.
  임계는 **학습 정상의 표본외 점수**에서만 잡는다(평가 데이터를 보지 않는다).

**과적합 주의**: 지금 손에 있는 것은 Class1 하나다. 여러 설정 중 Class1 에서 가장
좋은 것을 고르면 그 자체가 Class1 에 맞춘 것이다. 그래서 비교표(--compare)는 소수의
원리 있는 후보만 두고, 선택은 교차검증 + 계산 예산 + 일반성(제품 결함 유형)으로 한다.

DAGM 폴더 구조(원본 배포 그대로):
    Class1/Train/0576.PNG ...          이미지
    Class1/Train/Label/Labels.txt      [id, 결함여부(0/1), 파일명, ?, 마스크명]
    Class1/Train/Label/0595_label.PNG  결함 마스크
    Class1/Test/ ...                   동일 구조

사용:
    # 설정 하나
    python -m vision.tools.dagm_benchmark --class-dir <...>/Class1 \\
        [--feature-set v2] [--grid 8] [--no-overlap] [--topk 2] [--calib image] \\
        [--out-model models/dagm_class1.npz] [--md report.md] [--json r.json]
    # 비교표(DAGM_RESULT.md 재생성)
    python -m vision.tools.dagm_benchmark --class-dir <...>/Class1 --compare \\
        --md vision/models/benchmarks/DAGM_RESULT.md \\
        --json vision/models/benchmarks/dagm_class1.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

import cv2  # noqa: E402

from vision.models.train_anomaly import (  # noqa: E402
    _mahalanobis,
    fit_model,
    save_model,
)
from vision.surface.anomaly import (  # noqa: E402
    DEFAULT_FEATURE_SET,
    DEFAULT_PATCH_GRID,
    DEFAULT_PATCH_OVERLAP,
    DEFAULT_SCORE_TOPK,
    FEATURE_SET_V1,
    FEATURE_SET_V2,
    FEATURE_SETS,
    image_score,
    patch_features,
)

LABELS_NAME = "Labels.txt"

#: 라즈베리파이4 환산 배수(가정). 개발 PC(x86, 4스레드) 실측 × 이 값 = 파이4 추정.
#: 파이4(Cortex-A72 1.5GHz, NEON)는 같은 OpenCV/numpy 연산에서 대략 4~8배 느리다는
#: 것이 통상 관찰이고, 여기서는 그 안쪽 값 5를 쓴다. **추정이지 실측이 아니다** —
#: 현장에서는 vision.tools.bench_anomaly 로 파이에서 직접 잰다.
PI4_SLOWDOWN = 5.0

#: 속도를 재는 크기(가로x세로). 512x512 = DAGM 원본. 1728x96 = 기본 촬영
#: (2304x1296)에서 튜브 1개 표면 ROI(길이 ~85% − 양끝 6% 인셋, OD ~95px).
#: 1728x64·1728x48 = 다발(튜브 12~20개) 한 개의 표면 crop.
TIMING_SIZES: tuple[tuple[int, int], ...] = ((512, 512), (1728, 96), (1728, 64), (1728, 48))


@dataclass
class DagmItem:
    """DAGM 이미지 1장."""

    path: Path
    is_defect: bool
    mask: Optional[Path] = None


@dataclass
class BenchConfig:
    """비교할 설정 하나."""

    name: str
    feature_set: str = DEFAULT_FEATURE_SET
    grid: int = DEFAULT_PATCH_GRID
    overlap: bool = DEFAULT_PATCH_OVERLAP
    topk: int = DEFAULT_SCORE_TOPK
    #: image = 이미지 점수 분포에서 임계(새 방식) / patch = 패치 거리 분포(구 방식).
    calib: str = "image"

    def normalized(self) -> "BenchConfig":
        """v1 은 겹침·top-k 를 쓰지 않는다(구 모델 채점과 같게)."""
        if self.feature_set == FEATURE_SET_V1:
            return BenchConfig(self.name, self.feature_set, self.grid, False, 1, self.calib)
        return self


@dataclass
class BenchmarkResult:
    """벤치마크 1회 결과(A = 공식 분할, cv_* = 교차검증)."""

    class_name: str
    config: str = ""
    feature_set: str = FEATURE_SET_V1
    patch_grid: int = 1
    patch_overlap: bool = False
    score_topk: int = 1
    threshold_level: str = "patch"
    percentile: float = 99.0
    n_train_normal: int = 0
    n_test_normal: int = 0
    n_test_defect: int = 0
    threshold: float = 0.0
    threshold_basis: str = ""
    auroc: Optional[float] = None
    accuracy_pct: Optional[float] = None
    precision_pct: Optional[float] = None
    recall_pct: Optional[float] = None
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    cv_folds: int = 0
    cv_auroc_mean: Optional[float] = None
    cv_auroc_std: Optional[float] = None
    cv_auroc_min: Optional[float] = None
    cv_recall_pct: Optional[float] = None
    cv_precision_pct: Optional[float] = None
    cv_fpr_pct: Optional[float] = None
    #: 크기("WxH") → 이미지 1장 기술자+채점 중앙값(ms, 이 PC).
    ms_per_image: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["class_name"] = str(self.class_name)
        return d


def parse_labels(split_dir: str | Path) -> list[DagmItem]:
    """DAGM Labels.txt 를 읽어 (이미지, 결함여부, 마스크) 목록을 만든다.

    Labels.txt 가 없으면 폴더의 이미지를 전부 정상으로 본다 — 정상만 모아둔
    폴더를 그대로 넘기는 경우를 지원하기 위함이다.
    """
    split = Path(split_dir)
    label_dir = split / "Label"
    labels = label_dir / LABELS_NAME
    if not labels.is_file():
        imgs = sorted(p for p in split.glob("*.PNG")) + sorted(split.glob("*.png"))
        return [DagmItem(path=p, is_defect=False) for p in imgs]

    items: list[DagmItem] = []
    for raw in labels.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = [c.strip() for c in raw.split("\t")]
        # 첫 줄은 클래스 번호 한 칸이라 건너뛴다. 유효 행은 최소 3칸.
        if len(parts) < 3 or not parts[0].isdigit() or parts[1] not in ("0", "1"):
            continue
        img = split / parts[2]
        if not img.is_file():
            continue
        defect = parts[1] == "1"
        mask = None
        if defect and len(parts) >= 5 and parts[4] and parts[4] != "0":
            cand = label_dir / parts[4]
            mask = cand if cand.is_file() else None
        items.append(DagmItem(path=img, is_defect=defect, mask=mask))
    return items


def _auroc(scores: np.ndarray, labels: np.ndarray) -> Optional[float]:
    """ROC AUC — 순위 기반(Mann-Whitney U). 외부 의존 없이 계산한다.

    임계와 무관한 지표라 "임계를 잘 잡았는가" 와 "분리가 되는가" 를 분리해
    볼 수 있다. 둘을 섞으면 임계만 손봐서 좋아 보이게 만들 수 있다.
    """
    pos = scores[labels == 1]
    neg = scores[labels == 0]
    if pos.size == 0 or neg.size == 0:
        return None
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(scores.size, dtype=np.float64)
    ranks[order] = np.arange(1, scores.size + 1, dtype=np.float64)
    # 동점은 평균 순위로(동점을 유리하게 세면 AUROC 가 부풀려진다).
    uniq, inv, counts = np.unique(scores, return_inverse=True, return_counts=True)
    if counts.max() > 1:
        sums = np.zeros(uniq.size, dtype=np.float64)
        np.add.at(sums, inv, ranks)
        ranks = (sums / counts)[inv]
    r_pos = ranks[labels == 1].sum()
    return float((r_pos - pos.size * (pos.size + 1) / 2.0) / (pos.size * neg.size))


def _image_features(path: Path, cfg: BenchConfig) -> Optional[np.ndarray]:
    """DAGM 이미지 → 패치 기술자. **파이프용 ROI 전처리를 태우지 않는다.**

    운영 학습(train_anomaly)은 preprocess 로 제품 표면 ROI 를 먼저 잘라낸다.
    프레임 안에 파이프가 놓여 있기 때문이다. 그런데 DAGM 은 프레임 전체가 곧
    표면이라 잘라낼 제품이 없다. 그대로 태우면 전처리가 엉뚱한 영역을 ROI 로
    잡아 결함이 잘려 나가고, 벤치마크가 엔진이 아니라 전처리를 재게 된다
    (실측: 같은 8x8 격자에서 전처리 경유 AUROC 0.729, 원본 직접 1.000).
    """
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        return None
    feats = patch_features(
        img, None, feature_set=cfg.feature_set, grid=cfg.grid, overlap=cfg.overlap
    )
    return feats if feats.shape[0] else None


def _fit(feats: Sequence[np.ndarray], cfg: BenchConfig, *, percentile: float,
         reg: float, margin: float) -> dict:
    """정상 이미지 기술자 목록 → 모델(임계 방식은 cfg.calib)."""
    X = np.vstack(feats).astype(np.float64)
    if cfg.calib == "image":
        groups = np.concatenate(
            [np.full(f.shape[0], j, dtype=np.int64) for j, f in enumerate(feats)]
        )
        model = fit_model(
            X, percentile=percentile, reg=reg, margin=margin,
            groups=groups, topk=cfg.topk,
        )
    else:
        model = fit_model(X, percentile=percentile, reg=reg, margin=margin)
    model["patch_grid"] = int(max(1, cfg.grid))
    model["feature_set"] = cfg.feature_set
    model["patch_overlap"] = bool(cfg.overlap)
    model["score_topk"] = int(cfg.topk)
    return model


def _score(feats: Sequence[np.ndarray], model: dict, topk: int) -> np.ndarray:
    """이미지마다 상위 k 패치 거리 평균(추론과 같은 image_score)."""
    return np.array(
        [image_score(_mahalanobis(f, model["mean"], model["cov_inv"]), topk) for f in feats],
        dtype=np.float64,
    )


def _confusion(scores: np.ndarray, labels: np.ndarray, thr: float) -> tuple[int, int, int, int]:
    pred = scores >= thr
    tp = int((pred & (labels == 1)).sum())
    fp = int((pred & (labels == 0)).sum())
    tn = int((~pred & (labels == 0)).sum())
    fn = int((~pred & (labels == 1)).sum())
    return tp, fp, tn, fn


def _pct(num: int, den: int) -> Optional[float]:
    return round(num / den * 100.0, 2) if den else None


def time_config(
    cfg: BenchConfig,
    *,
    sizes: Sequence[tuple[int, int]] = TIMING_SIZES,
    sample: Optional[np.ndarray] = None,
    repeat: int = 15,
) -> dict[str, float]:
    """크기별 이미지 1장 기술자+채점 중앙값(ms). 첫 회는 버린다(캐시·스레드 워밍업).

    sample 을 주면 그 텍스처를 타일로 이어 붙여 크기를 맞춘다(실제 질감에 가까움).
    채점(Mahalanobis + image_score)도 포함한다 — 추론에서 실제로 도는 몫 전부.
    """
    cfg = cfg.normalized()
    if sample is None:
        rng = np.random.default_rng(12345)
        sample = rng.integers(60, 190, size=(256, 256), dtype=np.int16).astype(np.uint8)
    if sample.ndim == 2:
        sample = cv2.cvtColor(sample, cv2.COLOR_GRAY2BGR)
    out: dict[str, float] = {}
    for w, h in sizes:
        reps_y = int(np.ceil(h / sample.shape[0]))
        reps_x = int(np.ceil(w / sample.shape[1]))
        img = np.ascontiguousarray(np.tile(sample, (reps_y, reps_x, 1))[:h, :w])
        f0 = patch_features(img, None, feature_set=cfg.feature_set, grid=cfg.grid,
                            overlap=cfg.overlap)
        d = f0.shape[1]
        mean = np.zeros(d)
        prec = np.eye(d)
        times: list[float] = []
        for _ in range(max(1, repeat) + 1):
            t0 = time.perf_counter()
            f = patch_features(img, None, feature_set=cfg.feature_set, grid=cfg.grid,
                               overlap=cfg.overlap)
            image_score(_mahalanobis(f, mean, prec), cfg.topk)
            times.append((time.perf_counter() - t0) * 1000.0)
        times = sorted(times[1:])
        out[f"{w}x{h}"] = round(times[len(times) // 2], 2)
    return out


def run_benchmark(
    class_dir: str | Path,
    *,
    percentile: float = 99.0,
    reg: float = 0.1,
    margin: float = 1.0,
    max_train: Optional[int] = None,
    grid: int = DEFAULT_PATCH_GRID,
    feature_set: str = DEFAULT_FEATURE_SET,
    overlap: bool = DEFAULT_PATCH_OVERLAP,
    topk: int = DEFAULT_SCORE_TOPK,
    calib: str = "image",
    cv_folds: int = 5,
    timing: bool = False,
    name: str = "",
    _cache: Optional[dict] = None,
) -> tuple[BenchmarkResult, dict]:
    """한 클래스·한 설정: 공식 분할(A) + 교차검증(B). (결과, 공식 분할 모델)."""
    cfg = BenchConfig(name or "", feature_set, grid, overlap, topk, calib).normalized()
    root = Path(class_dir)
    res = BenchmarkResult(
        class_name=root.name, config=cfg.name, feature_set=cfg.feature_set,
        patch_grid=int(max(1, cfg.grid)), patch_overlap=cfg.overlap,
        score_topk=cfg.topk, threshold_level=cfg.calib, percentile=percentile,
    )

    train_items = parse_labels(root / "Train")
    test_items = parse_labels(root / "Test")
    if not train_items:
        raise FileNotFoundError(f"학습 이미지를 찾지 못했다: {root / 'Train'}")

    # 학습은 **정상만** 쓴다(비지도 이상탐지의 전제).
    train_normal = [i for i in train_items if not i.is_defect]
    if max_train:
        train_normal = train_normal[:max_train]

    key = (cfg.feature_set, cfg.grid, cfg.overlap)
    cache = _cache if _cache is not None else {}
    feats_by_path: dict[Path, Optional[np.ndarray]] = cache.setdefault(key, {})

    def feats_of(items: Sequence[DagmItem]) -> tuple[list[np.ndarray], list[int]]:
        out, kept = [], []
        for idx, it in enumerate(items):
            if it.path not in feats_by_path:
                feats_by_path[it.path] = _image_features(it.path, cfg)
            f = feats_by_path[it.path]
            if f is not None:
                out.append(f)
                kept.append(idx)
        return out, kept

    tr_feats, _ = feats_of(train_normal)
    res.n_train_normal = len(tr_feats)
    if res.n_train_normal < 2:
        raise ValueError(f"정상 학습 이미지가 부족하다({res.n_train_normal}장).")

    model = _fit(tr_feats, cfg, percentile=percentile, reg=reg, margin=margin)
    res.threshold = float(model["threshold"])
    res.threshold_basis = str(model["threshold_basis"])

    if not test_items:
        res.notes.append("테스트 세트가 없어 평가를 건너뛰었다(학습만 수행).")
        return res, model

    te_feats, kept = feats_of(test_items)
    if len(kept) != len(test_items):
        res.notes.append(f"테스트 이미지 {len(test_items)}장 중 {len(kept)}장만 읽혔다.")
    labels = np.array([1 if test_items[i].is_defect else 0 for i in kept], dtype=int)
    scores = _score(te_feats, model, cfg.topk)

    res.n_test_normal = int((labels == 0).sum())
    res.n_test_defect = int((labels == 1).sum())
    res.auroc = _auroc(scores, labels)
    res.tp, res.fp, res.tn, res.fn = _confusion(scores, labels, res.threshold)
    total = res.tp + res.fp + res.tn + res.fn
    res.accuracy_pct = _pct(res.tp + res.tn, total)
    res.precision_pct = _pct(res.tp, res.tp + res.fp)
    res.recall_pct = _pct(res.tp, res.tp + res.fn)

    # --- B. 교차검증: 정상 전부를 폴드로, 결함은 매 폴드 평가에만 ------------
    if cv_folds and cv_folds >= 2:
        normals = tr_feats + [f for f, y in zip(te_feats, labels) if y == 0]
        defects = [f for f, y in zip(te_feats, labels) if y == 1]
        if len(normals) >= 2 * cv_folds and defects:
            aucs: list[float] = []
            tp = fp = tn = fn = 0
            idx = np.arange(len(normals))
            for f in range(cv_folds):
                ho = idx[f::cv_folds]
                trn = np.setdiff1d(idx, ho)
                m = _fit([normals[i] for i in trn], cfg, percentile=percentile,
                         reg=reg, margin=margin)
                ev = [normals[i] for i in ho] + defects
                y = np.array([0] * len(ho) + [1] * len(defects))
                s = _score(ev, m, cfg.topk)
                a = _auroc(s, y)
                if a is not None:
                    aucs.append(a)
                c = _confusion(s, y, float(m["threshold"]))
                tp, fp, tn, fn = tp + c[0], fp + c[1], tn + c[2], fn + c[3]
            res.cv_folds = int(cv_folds)
            if aucs:
                res.cv_auroc_mean = float(np.mean(aucs))
                res.cv_auroc_std = float(np.std(aucs))
                res.cv_auroc_min = float(np.min(aucs))
            res.cv_recall_pct = _pct(tp, tp + fn)
            res.cv_precision_pct = _pct(tp, tp + fp)
            res.cv_fpr_pct = _pct(fp, fp + tn)

    if timing:
        sample = cv2.imread(str(train_normal[0].path), cv2.IMREAD_GRAYSCALE)
        res.ms_per_image = time_config(cfg, sample=sample, repeat=21)

    if res.n_test_defect == 0:
        res.notes.append("테스트에 결함 표본이 없어 재현율·AUROC 를 낼 수 없다.")
    if res.n_train_normal < 50:
        res.notes.append(
            f"정상 학습 표본이 적다({res.n_train_normal}장). 공분산·임계 추정이 불안정해 "
            "수치를 과신하면 안 된다."
        )
    return res, model


#: 비교표 후보. **소수의 원리 있는 후보만** 둔다(과적합 주의 — 모듈 docstring).
COMPARE_CONFIGS: tuple[BenchConfig, ...] = (
    BenchConfig("구 기본값: v1 격자1", FEATURE_SET_V1, 1, False, 1, "patch"),
    BenchConfig("v1 격자8 (구 최고, 패치 임계)", FEATURE_SET_V1, 8, False, 1, "patch"),
    BenchConfig("v1 격자8 + 이미지 임계", FEATURE_SET_V1, 8, False, 1, "image"),
    BenchConfig("v2 격자1", FEATURE_SET_V2, 1, False, 1, "image"),
    BenchConfig("v2 격자4 겹침 top2", FEATURE_SET_V2, 4, True, 2, "image"),
    BenchConfig("v2 격자8 겹침없음 top1", FEATURE_SET_V2, 8, False, 1, "image"),
    BenchConfig("v2 격자8 겹침 top1", FEATURE_SET_V2, 8, True, 1, "image"),
    BenchConfig("**v2 격자8 겹침 top2 (새 기본값)**", FEATURE_SET_V2, 8, True, 2, "image"),
    BenchConfig("v2 격자16 겹침 top2", FEATURE_SET_V2, 16, True, 2, "image"),
)


def run_compare(
    class_dir: str | Path,
    *,
    percentile: float = 99.0,
    alt_percentile: Optional[float] = 95.0,
    reg: float = 0.1,
    margin: float = 1.0,
    cv_folds: int = 5,
    timing: bool = True,
    configs: Sequence[BenchConfig] = COMPARE_CONFIGS,
) -> list[BenchmarkResult]:
    """비교표용 — 후보마다 run_benchmark. alt_percentile 이면 그 임계로도 한 번 더.

    기술자는 (세트, 격자, 겹침) 별로 한 번만 계산해 재사용한다(_cache).
    """
    cache: dict = {}
    out: list[BenchmarkResult] = []
    for cfg in configs:
        res, _ = run_benchmark(
            class_dir, percentile=percentile, reg=reg, margin=margin,
            grid=cfg.grid, feature_set=cfg.feature_set, overlap=cfg.overlap,
            topk=cfg.topk, calib=cfg.calib, cv_folds=cv_folds, timing=timing,
            name=cfg.name, _cache=cache,
        )
        out.append(res)
    if alt_percentile is not None:
        for cfg in configs:
            if cfg.calib != "image":
                continue
            res, _ = run_benchmark(
                class_dir, percentile=alt_percentile, reg=reg, margin=margin,
                grid=cfg.grid, feature_set=cfg.feature_set, overlap=cfg.overlap,
                topk=cfg.topk, calib=cfg.calib, cv_folds=cv_folds, timing=False,
                name=cfg.name, _cache=cache,
            )
            out.append(res)
    return out


def _f3(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.3f}"


def _p1(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.1f}%"


#: --compare 보고서 뒤에 붙는 선택 근거·한계. **선택 당시(2026-10-20, Class1)의 판단**
#: 이다 — 다른 데이터로 다시 돌리면 표의 숫자는 바뀌어도 이 글은 그대로다.
#: '탐색' 표시 숫자는 이 도구의 후보 밖 실험(같은 데이터·같은 평가 B)에서 쟀다.
COMPARE_NOTES = """## 선택 근거 (2026-10-20, Class1)

1. **임계 단위부터 고쳤다.** 구 방식은 임계를 패치 거리의 99% 로 잡고 이미지는 최악
   패치로 채점했다. 패치가 P 개면 정상 이미지가 임계를 넘을 확률이 1−0.99^P 다 —
   격자 8(64 패치)에서 B 오검률 39.7%. 구 '최고' 재현율 96% 는 이 낮은 임계 덕이었다.
   같은 v1 격자 8 을 이미지 임계로 바꾸면 오검률 1.4%·재현율 7% — 분리도(AUROC 0.85)가
   모자랐던 것이다. 이제 임계는 추론과 같은 이미지 점수의 표본외 분포에서 잡는다.
2. **기술자(v2)**: 질감 에너지(국소 대비, DoG 3대역, 그래디언트 크기·4방향) + 색, 에너지는
   log1p. 화소 맵을 한 번 만들고 패치는 평균 풀링(분위수 없음). 탐색: 로그 없이 B AUROC
   0.950 → 로그 0.990. 밝기(L 평균·편차)를 더하면 0.977~0.978 로 떨어졌다 — 절대 밝기는
   조명·곡면 위치에 좌우되고, 밝기 기반 유분기는 고전 CV 가 이미 본다.
3. **격자·겹침·집계**: 격자 8 > 4 ≫ 1. 반 패치 겹침 + 상위 2 패치 평균이 같은 오검률에서
   재현율이 가장 높았다(탐색: 겹침 없이 top2 는 손해 — 작은 결함은 한 패치에만 있다).
4. **격자 8 vs 16**: 16 이 Class1 에서 근소하게 높지만 폴드 표준편차 안이고, 95% 운영점에서는
   재현율 순서가 뒤집힌다. 또 짧은 변이 288px 미만인 튜브 crop 에서는 8 과 16 이 같은
   배치다(짧은 변 ÷ 32px 상한). 차이가 나는 큰 영역에서는 큰 패치의 통계가 안정적이라 8.
5. **속도**: v2 비용은 패치 수가 아니라 화소 수에 비례한다. 튜브 1개 표면(1728x96)에서
   v2 와 고전 CV(predict 가 항상 먼저 돌린다)를 합쳐도 파이4 추정 100ms 안(위 표·고전 CV
   줄). v1 격자 8 은 같은 크기에서 그 혼자 예산을 넘었다 — 그래서 구 기본값이 격자 1 이었다.
6. **가는 튜브 crop**: v1 의 grid x grid 는 짧은 변 32px 제한 때문에 다발 튜브 1개(1728x48)
   에서 1칸으로 떨어졌다 — 다발 검사에서는 격자 설정과 무관하게 표면 전체를 한 덩어리로
   봤다. v2 는 짧은 변 기준 정사각형 패치(1728x48 → 36칸, 겹침 71개).

## 한계

- **Class1 한 클래스**(정상 73·결함 27)이고 후보 선택도 같은 데이터로 했다 → 낙관 편향이
  있다. 일반화는 DAGM 다른 클래스와 실제 제품 사진으로 확인해야 한다.
- Class1 결함은 질감이 뭉개진 얼룩이다. 스크래치(방향 에너지)·변색(색)·유분기 축은 이
  벤치마크로 검증되지 않았다. 질감 그룹만 쓰면 Class1 에서 더 높다(탐색: B 0.997) — 그래도
  방향·색 축을 남긴 것은 제품 결함(SCR/DIS) 때문이고, Class1 에 맞춘 선택을 피한 것이다.
- 파이4 시간은 ×5 **가정**이다. 현장 파이에서 `vision.tools.bench_anomaly` 로 실측한다.
- 모든 패치가 하나의 가우시안을 공유한다(위치 무관). 튜브는 단면 위치(정반사 띠)마다 정상
  질감이 다를 수 있다 — 실제 사진으로 확인한 뒤 위치별 분포를 검토한다.
- 99% 임계 = '정상의 약 1% 재확인' 목표. 정상 이미지가 적으면(권장 200개 이상) 가장 튀는
  몇 장이 임계를 정한다 — 학습 CLI 가 경고한다. 95% 로 낮추면 재현율이 크게 오른다(위 표).
- `dagm_class1_grid8.npz` 는 구 형식(v1·격자 8, feature_set 키 없음) 모델로, 하위호환 시험
  고정물로 남겨 둔다(제품에는 못 쓴다).
"""


def time_classical(
    sizes: Sequence[tuple[int, int]] = TIMING_SIZES,
    *,
    sample: Optional[np.ndarray] = None,
    repeat: int = 15,
) -> dict[str, float]:
    """고전 CV(analyze_surface) 크기별 중앙값(ms). predict 는 이상탐지 전에 항상 이것을 돌린다."""
    from aivis_types import ItemMaster

    from vision.surface.classical import analyze_surface

    item = ItemMaster(
        item_code="DAGM", item_name="DAGM", ref_length_mm=250.0,
        tol_plus_mm=0.1, tol_minus_mm=0.1, px_to_mm_scale=0.1,
    )
    if sample is None:
        rng = np.random.default_rng(12345)
        sample = rng.integers(60, 190, size=(256, 256), dtype=np.int16).astype(np.uint8)
    if sample.ndim == 2:
        sample = cv2.cvtColor(sample, cv2.COLOR_GRAY2BGR)
    out: dict[str, float] = {}
    for w, h in sizes:
        reps_y = int(np.ceil(h / sample.shape[0]))
        reps_x = int(np.ceil(w / sample.shape[1]))
        img = np.ascontiguousarray(np.tile(sample, (reps_y, reps_x, 1))[:h, :w])
        times: list[float] = []
        for _ in range(max(1, repeat) + 1):
            t0 = time.perf_counter()
            analyze_surface(img, item)
            times.append((time.perf_counter() - t0) * 1000.0)
        times = sorted(times[1:])
        out[f"{w}x{h}"] = round(times[len(times) // 2], 2)
    return out


def to_markdown(
    results: Sequence[BenchmarkResult],
    *,
    pi_factor: float = PI4_SLOWDOWN,
    classical_ms: Optional[dict[str, float]] = None,
    notes: Optional[str] = None,
) -> str:
    """결과 → 한국어 마크다운(비교표 + 혼동행렬 + 선택 근거 + 유의사항)."""
    lines = [
        "# DAGM 2007 이상탐지 벤치마크",
        "",
        "PaDiM-lite(Mahalanobis) 엔진을 공개 벤치마크로 검증한 결과입니다.",
        "재현: `python -m vision.tools.dagm_benchmark --class-dir <DAGM>/Class1 --compare`",
        "",
        "> **이 모델은 에이엠피 제품에 쓸 수 없습니다.** 학습한 것은 DAGM 텍스처의",
        "> 정상 분포이지 알루미늄 헤더파이프의 정상 분포가 아닙니다. 또 DAGM 은",
        "> 흑백이라 변색 판정의 근거가 되는 색 기술자를 학습하지 못합니다.",
        "> 여기서 확인하는 것은 **방법의 타당성**입니다.",
        "",
    ]
    if not results:
        return "\n".join(lines) + "\n"
    r0 = results[0]
    lines += [
        f"- 데이터: {r0.class_name} — 학습 정상 {r0.n_train_normal} / 평가 정상 "
        f"{r0.n_test_normal}·결함 {r0.n_test_defect}. 결함은 학습에 쓰지 않습니다.",
        "- **A(공식 분할)**: Train 정상으로 학습 → Test 평가. "
        "**B(교차검증)**: 정상 전부를 "
        f"{r0.cv_folds or '-'}폴드로 나눠 폴드마다 나머지로 학습, 그 폴드 정상+결함 전부 평가"
        "(AUROC 평균±표준편차, 재현율·정밀도·오검률은 폴드 합산).",
        "- 임계는 학습 정상의 **표본외** 점수 백분위(평가 데이터를 보지 않음). "
        "'이미지 임계' = 추론과 같은 이미지 점수 분포, '패치 임계' = 패치 거리 분포(구 방식).",
        f"- 속도: 이미지 1장 기술자+채점 중앙값(마스크 없이), 이 PC 실측. 파이4 추정 = ×{pi_factor:g}"
        "(가정 — 현장 실측은 `vision.tools.bench_anomaly`).",
        "",
    ]
    main = [r for r in results if r.percentile == r0.percentile]
    alt = [r for r in results if r.percentile != r0.percentile]
    size_keys = list(main[0].ms_per_image.keys()) if main and main[0].ms_per_image else []

    def table(rs: Sequence[BenchmarkResult], with_time: bool) -> list[str]:
        head = "| 설정 | 임계 | A AUROC | A 재현율 | A 정밀도 | B AUROC | B 재현율 | B 정밀도 | B 오검률 |"
        sep = "|---|---|---|---|---|---|---|---|---|"
        if with_time:
            for k in size_keys:
                head += f" ms {k} (파이4) |"
                sep += "---|"
        out = [head, sep]
        for r in rs:
            cv = (
                "-" if r.cv_auroc_mean is None
                else f"{r.cv_auroc_mean:.3f}±{(r.cv_auroc_std or 0.0):.3f}"
            )
            lvl = "이미지" if r.threshold_level == "image" else "패치"
            row = (
                f"| {r.config or r.feature_set} | {lvl} {r.percentile:g}% | {_f3(r.auroc)} | "
                f"{_p1(r.recall_pct)} | {_p1(r.precision_pct)} | {cv} | "
                f"{_p1(r.cv_recall_pct)} | {_p1(r.cv_precision_pct)} | {_p1(r.cv_fpr_pct)} |"
            )
            if with_time:
                for k in size_keys:
                    ms = r.ms_per_image.get(k)
                    row += " - |" if ms is None else f" {ms:.1f} ({ms * pi_factor:.0f}) |"
            out.append(row)
        return out

    lines += [f"## 비교표 (임계 = 표본외 {r0.percentile:g}%)", ""]
    lines += table(main, bool(size_keys))
    if classical_ms:
        parts = ", ".join(
            f"{k} {v:.1f}ms (파이4 {v * pi_factor:.0f})" for k, v in classical_ms.items()
        )
        lines += [
            "",
            f"고전 CV(analyze_surface — predict 가 이상탐지 전에 항상 돌림): {parts}. "
            "표면 엔진 시간 = 이 값 + 위 표의 이상탐지 시간.",
        ]
    if alt:
        lines += [
            "",
            f"## 같은 후보, 임계만 {alt[0].percentile:g}% (재확인을 더 받는 운영점)",
            "",
        ]
        lines += table(alt, False)
    lines += ["", "## 혼동행렬 (A, 공식 분할)", ""]
    lines += ["| 설정 | 임계 백분위 | TP | FP | TN | FN | 임계 | 임계 산출 |",
              "|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(
            f"| {r.config or r.feature_set} | {r.percentile:g}% | {r.tp} | {r.fp} | {r.tn} | "
            f"{r.fn} | {r.threshold:.3f} | {r.threshold_basis} |"
        )
    if notes:
        lines += ["", notes.rstrip()]
    extra = sorted({n for r in results for n in r.notes})
    if extra:
        lines += ["", "## 유의사항", ""]
        lines += [f"- {n}" for n in extra]
    return "\n".join(lines) + "\n"


def _main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dagm_benchmark", description="DAGM 2007 로 이상탐지 엔진 검증"
    )
    ap.add_argument(
        "--class-dir",
        action="append",
        required=True,
        help="DAGM 클래스 폴더(Train/Test 포함). 여러 번 지정 가능",
    )
    ap.add_argument("--compare", action="store_true",
                    help="후보 설정 비교표(COMPARE_CONFIGS)를 만든다")
    ap.add_argument("--out-model", default=None, help="npz 저장 경로(단일 클래스·단일 설정)")
    ap.add_argument("--md", default=None, help="마크다운 보고서 경로")
    ap.add_argument("--json", default=None, help="JSON 결과 경로")
    ap.add_argument("--percentile", type=float, default=99.0)
    ap.add_argument("--alt-percentile", type=float, default=95.0,
                    help="--compare 에서 임계만 바꿔 한 번 더 보는 백분위(0 이면 생략)")
    ap.add_argument("--reg", type=float, default=0.1)
    ap.add_argument("--margin", type=float, default=1.0)
    ap.add_argument(
        "--max-train", type=int, default=None, help="정상 학습 표본 상한(실험용)"
    )
    ap.add_argument("--feature-set", choices=FEATURE_SETS, default=DEFAULT_FEATURE_SET)
    ap.add_argument(
        "--grid",
        type=int,
        default=DEFAULT_PATCH_GRID,
        help="패치 격자(작은 국소 결함 탐지력 향상)",
    )
    ap.add_argument("--no-overlap", action="store_true", help="패치를 겹치지 않는다")
    ap.add_argument("--topk", type=int, default=DEFAULT_SCORE_TOPK,
                    help="이미지 점수 = 상위 k 패치 평균")
    ap.add_argument("--calib", choices=("image", "patch"), default="image",
                    help="임계 산출 단위(image=이미지 점수, patch=패치 거리·구 방식)")
    ap.add_argument("--cv-folds", type=int, default=5, help="교차검증 폴드(0 이면 생략)")
    ap.add_argument("--timing", action="store_true", help="크기별 처리시간도 잰다")
    ap.add_argument("--pi-factor", type=float, default=PI4_SLOWDOWN,
                    help=f"파이4 환산 배수(가정, 기본 {PI4_SLOWDOWN:g})")
    args = ap.parse_args(argv)

    results: list[BenchmarkResult] = []
    classical_ms: Optional[dict[str, float]] = None
    for cd in args.class_dir:
        if args.compare:
            if classical_ms is None:
                first = next((i.path for i in parse_labels(Path(cd) / "Train")), None)
                sample = (
                    cv2.imread(str(first), cv2.IMREAD_GRAYSCALE) if first is not None else None
                )
                classical_ms = time_classical(sample=sample)
            results.extend(
                run_compare(
                    cd, percentile=args.percentile,
                    alt_percentile=args.alt_percentile or None,
                    reg=args.reg, margin=args.margin, cv_folds=args.cv_folds,
                )
            )
            continue
        res, model = run_benchmark(
            cd,
            percentile=args.percentile,
            reg=args.reg,
            margin=args.margin,
            max_train=args.max_train,
            grid=args.grid,
            feature_set=args.feature_set,
            overlap=not args.no_overlap,
            topk=args.topk,
            calib=args.calib,
            cv_folds=args.cv_folds,
            timing=args.timing,
        )
        results.append(res)
        if args.out_model and len(args.class_dir) == 1:
            save_model(model, args.out_model, item_code=res.class_name)

    if args.md:
        md = to_markdown(
            results, pi_factor=args.pi_factor, classical_ms=classical_ms,
            notes=COMPARE_NOTES if args.compare else None,
        )
        Path(args.md).write_text(md, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(
            json.dumps([r.as_dict() for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps([r.as_dict() for r in results], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
