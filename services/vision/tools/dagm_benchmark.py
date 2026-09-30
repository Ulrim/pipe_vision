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
  2. DAGM 은 흑백이다. 기술자 19개 중 색 관련 6개(a/b 평균·표준편차,
     colorfulness, ab_dist)는 학습이 안 된다. 변색(DIS) 판정은 색이 근거인데
     그 축을 못 배운다.
따라서 여기서 얻는 것은 **모델이 아니라 방법의 타당성과 하이퍼파라미터**다
(정칙화 계수, 임계 백분위, 표본수 대비 성능). 제품 모델은 에이엠피 정상품
사진으로 다시 학습해야 한다.

DAGM 폴더 구조(원본 배포 그대로):
    Class1/Train/0576.PNG ...          이미지
    Class1/Train/Label/Labels.txt      [id, 결함여부(0/1), 파일명, ?, 마스크명]
    Class1/Train/Label/0595_label.PNG  결함 마스크
    Class1/Test/ ...                   동일 구조

사용:
    python -m vision.tools.dagm_benchmark --class-dir <...>/Class1 \\
        [--out-model models/dagm_class1.npz] [--md report.md]
"""
from __future__ import annotations

import argparse
import json
import sys
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
from vision.surface.anomaly import patch_descriptors  # noqa: E402


def _descriptors(paths: Sequence[Path], *, grid: int) -> np.ndarray:
    """DAGM 이미지 → 기술자 행렬. **파이프용 ROI 전처리를 태우지 않는다.**

    운영 학습(train_anomaly)은 preprocess 로 제품 표면 ROI 를 먼저 잘라낸다.
    프레임 안에 파이프가 놓여 있기 때문이다. 그런데 DAGM 은 프레임 전체가 곧
    표면이라 잘라낼 제품이 없다. 그대로 태우면 전처리가 엉뚱한 영역을 ROI 로
    잡아 결함이 잘려 나가고, 벤치마크가 엔진이 아니라 전처리를 재게 된다
    (실측: 같은 8x8 격자에서 전처리 경유 AUROC 0.729, 원본 직접 1.000).
    """
    rows: list[np.ndarray] = []
    for p in paths:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            continue
        rows.append(patch_descriptors(img, None, grid=grid))
    if not rows:
        return np.empty((0, 0), dtype=np.float64)
    return np.vstack(rows).astype(np.float64)

LABELS_NAME = "Labels.txt"


@dataclass
class DagmItem:
    """DAGM 이미지 1장."""

    path: Path
    is_defect: bool
    mask: Optional[Path] = None


@dataclass
class BenchmarkResult:
    """벤치마크 1회 결과."""

    class_name: str
    patch_grid: int = 1
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


def run_benchmark(
    class_dir: str | Path,
    *,
    percentile: float = 99.0,
    reg: float = 0.1,
    margin: float = 1.0,
    max_train: Optional[int] = None,
    grid: int = 1,
) -> tuple[BenchmarkResult, dict]:
    """한 클래스에 대해 학습(정상만) → 평가(정상+결함)."""
    root = Path(class_dir)
    res = BenchmarkResult(class_name=root.name)

    train_items = parse_labels(root / "Train")
    test_items = parse_labels(root / "Test")
    if not train_items:
        raise FileNotFoundError(f"학습 이미지를 찾지 못했다: {root / 'Train'}")

    # 학습은 **정상만** 쓴다(비지도 이상탐지의 전제).
    train_normal = [i.path for i in train_items if not i.is_defect]
    if max_train:
        train_normal = train_normal[:max_train]
    res.n_train_normal = len(train_normal)
    if res.n_train_normal < 2:
        raise ValueError(f"정상 학습 이미지가 부족하다({res.n_train_normal}장).")

    X = _descriptors(train_normal, grid=grid)
    model = fit_model(X, percentile=percentile, reg=reg, margin=margin)
    model["patch_grid"] = int(max(1, grid))
    res.patch_grid = int(max(1, grid))
    res.threshold = float(model["threshold"])
    res.threshold_basis = str(model["threshold_basis"])

    # 평가: 테스트 전량(정상+결함)에 거리 계산.
    if not test_items:
        res.notes.append("테스트 세트가 없어 평가를 건너뛰었다(학습만 수행).")
        return res, model

    paths = [i.path for i in test_items]
    labels = np.array([1 if i.is_defect else 0 for i in test_items], dtype=int)
    # 이미지 1장당 패치가 여러 개다. 학습과 같은 격자로 뽑아 **최악 패치**를
    # 그 이미지의 점수로 삼는다(평균을 쓰면 정상 패치가 결함을 희석한다).
    scores_list: list[float] = []
    kept: list[int] = []
    for idx, path in enumerate(paths):
        Xi = _descriptors([path], grid=grid)
        if Xi.shape[0] == 0:
            continue
        scores_list.append(
            float(_mahalanobis(Xi, model["mean"], model["cov_inv"]).max())
        )
        kept.append(idx)
    if len(kept) != len(paths):
        res.notes.append(
            f"테스트 이미지 {len(paths)}장 중 {len(kept)}장만 읽혔다."
        )
    labels = labels[kept]
    scores = np.asarray(scores_list, dtype=np.float64)

    res.n_test_normal = int((labels == 0).sum())
    res.n_test_defect = int((labels == 1).sum())
    res.auroc = _auroc(scores, labels)

    pred = (scores >= res.threshold).astype(int)
    res.tp = int(((pred == 1) & (labels == 1)).sum())
    res.fp = int(((pred == 1) & (labels == 0)).sum())
    res.tn = int(((pred == 0) & (labels == 0)).sum())
    res.fn = int(((pred == 0) & (labels == 1)).sum())
    total = res.tp + res.fp + res.tn + res.fn
    if total:
        res.accuracy_pct = round((res.tp + res.tn) / total * 100.0, 2)
    if res.tp + res.fp:
        res.precision_pct = round(res.tp / (res.tp + res.fp) * 100.0, 2)
    if res.tp + res.fn:
        res.recall_pct = round(res.tp / (res.tp + res.fn) * 100.0, 2)

    if res.n_test_defect == 0:
        res.notes.append("테스트에 결함 표본이 없어 재현율·AUROC 를 낼 수 없다.")
    if res.n_train_normal < 50:
        res.notes.append(
            f"정상 학습 표본이 적다({res.n_train_normal}장). 공분산 추정이 불안정해 "
            "수치를 과신하면 안 된다."
        )
    return res, model


def to_markdown(results: Sequence[BenchmarkResult]) -> str:
    lines = [
        "# DAGM 2007 이상탐지 벤치마크",
        "",
        "PaDiM-lite(Mahalanobis) 엔진을 공개 벤치마크로 검증한 결과입니다.",
        "",
        "> **이 모델은 에이엠피 제품에 쓸 수 없습니다.** 학습한 것은 DAGM 텍스처의",
        "> 정상 분포이지 알루미늄 헤더파이프의 정상 분포가 아닙니다. 또 DAGM 은",
        "> 흑백이라 변색 판정의 근거가 되는 색 기술자를 학습하지 못합니다.",
        "> 여기서 확인하는 것은 **방법의 타당성**입니다.",
        "",
        "| 클래스 | 패치격자 | 학습(정상) | 테스트 정상/결함 | AUROC | 정확도 | 정밀도 | 재현율 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        auroc = "-" if r.auroc is None else f"{r.auroc:.3f}"
        acc = "-" if r.accuracy_pct is None else f"{r.accuracy_pct:.1f}%"
        prec = "-" if r.precision_pct is None else f"{r.precision_pct:.1f}%"
        rec = "-" if r.recall_pct is None else f"{r.recall_pct:.1f}%"
        lines.append(
            f"| {r.class_name} | {r.patch_grid}x{r.patch_grid} | {r.n_train_normal} | "
            f"{r.n_test_normal}/{r.n_test_defect} | {auroc} | {acc} | {prec} | {rec} |"
        )
    lines.append("")
    lines.append("## 혼동행렬")
    lines.append("")
    lines.append("| 클래스 | TP | FP | TN | FN | 임계 | 임계 산출 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in results:
        lines.append(
            f"| {r.class_name} | {r.tp} | {r.fp} | {r.tn} | {r.fn} | "
            f"{r.threshold:.3f} | {r.threshold_basis} |"
        )
    notes = [(r.class_name, n) for r in results for n in r.notes]
    if notes:
        lines += ["", "## 유의사항", ""]
        lines += [f"- ({c}) {n}" for c, n in notes]
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
    ap.add_argument("--out-model", default=None, help="npz 저장 경로(단일 클래스)")
    ap.add_argument("--md", default=None, help="마크다운 보고서 경로")
    ap.add_argument("--json", default=None, help="JSON 결과 경로")
    ap.add_argument("--percentile", type=float, default=99.0)
    ap.add_argument("--reg", type=float, default=0.1)
    ap.add_argument("--margin", type=float, default=1.0)
    ap.add_argument(
        "--max-train", type=int, default=None, help="정상 학습 표본 상한(실험용)"
    )
    ap.add_argument(
        "--grid",
        type=int,
        default=1,
        help="표면을 grid x grid 패치로 나눠 학습·채점(작은 국소 결함 탐지력 향상)",
    )
    args = ap.parse_args(argv)

    results: list[BenchmarkResult] = []
    for cd in args.class_dir:
        res, model = run_benchmark(
            cd,
            percentile=args.percentile,
            reg=args.reg,
            margin=args.margin,
            max_train=args.max_train,
            grid=args.grid,
        )
        results.append(res)
        if args.out_model and len(args.class_dir) == 1:
            save_model(model, args.out_model, item_code=res.class_name)

    if args.md:
        Path(args.md).write_text(to_markdown(results), encoding="utf-8")
    if args.json:
        Path(args.json).write_text(
            json.dumps([r.as_dict() for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps([r.as_dict() for r in results], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
