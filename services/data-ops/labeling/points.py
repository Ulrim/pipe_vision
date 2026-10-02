"""단면 점(point) 라벨 — 크레이트 단면 계수의 정답셋 (부록 A.1/A.2).

**왜 따로 두는가.** `groundtruth.py` 는 프레임 하나에 클래스 코드(OK/LEN/OIL…)를
붙이는 구조다. 크레이트 단면 구도는 한 프레임에 튜브 단면이 수백 개 들어가므로
프레임 라벨로는 계수 정확도를 잴 수 없다. 단면마다 점이 필요하다.

**사람이 처음부터 수백 개를 찍게 하지 않는다.** 검출기가 먼저 찍어주고
(`<stem>.points.auto.json`), 사람은 그걸 복사한 정답 파일(`<stem>.points.json`)
에서 **틀린 것만 고친다.** 그래야 수백 개짜리 프레임도 현실적인 시간에 끝난다.

파일을 둘로 나눈 이유는 **채점 때문**이다. 사람이 제안 파일을 직접 고치면
제안이 사라져 검출기 성능을 잴 수 없다. auto 는 언제든 다시 만들 수 있고,
정답 파일은 **덮어쓰지 않는다**(사람 작업을 날리면 안 된다).

사이드카 형식:

    {
      "image": "crate_0001.jpg",
      "width": 1080, "height": 1420,
      "source": "auto" | "human",
      "detector": "count_bundle",      # auto 일 때 출처
      "points": [{"x": 123.0, "y": 45.0, "r": 18.3}],
      "note": ""
    }
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional, Sequence

import numpy as np

AUTO_SUFFIX = ".points.auto.json"
GT_SUFFIX = ".points.json"

_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp")


@dataclass(frozen=True)
class PointLabel:
    """단면 1개. r 은 반지름(px). 사람이 점만 찍으면 0 일 수 있다."""

    x: float
    y: float
    r: float = 0.0


@dataclass
class PointSet:
    image: str
    width: int
    height: int
    source: str = "auto"
    points: list[PointLabel] = field(default_factory=list)
    detector: Optional[str] = None
    note: str = ""

    @property
    def count(self) -> int:
        return len(self.points)


class PointLabelError(Exception):
    """사이드카 형식 오류."""


def auto_path(image_path: str) -> str:
    return os.path.splitext(image_path)[0] + AUTO_SUFFIX


def gt_path(image_path: str) -> str:
    return os.path.splitext(image_path)[0] + GT_SUFFIX


def iter_images(root: str) -> list[str]:
    out: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for n in sorted(filenames):
            if n.lower().endswith(_IMAGE_EXT):
                out.append(os.path.join(dirpath, n))
    return sorted(out)


def save_points(path: str, ps: PointSet) -> None:
    d: dict[str, Any] = asdict(ps)
    d["points"] = [asdict(p) if not isinstance(p, dict) else p for p in ps.points]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def load_points(path: str) -> PointSet:
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise PointLabelError(f"{path}: 읽을 수 없다 ({exc})") from exc
    if not isinstance(d, dict) or "points" not in d:
        raise PointLabelError(f"{path}: points 키가 없다")
    pts: list[PointLabel] = []
    for i, raw in enumerate(d["points"]):
        try:
            pts.append(
                PointLabel(
                    x=float(raw["x"]), y=float(raw["y"]), r=float(raw.get("r", 0.0))
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PointLabelError(f"{path}: points[{i}] 이 잘못됐다 ({exc})") from exc
    return PointSet(
        image=str(d.get("image", os.path.basename(path))),
        width=int(d.get("width", 0)),
        height=int(d.get("height", 0)),
        source=str(d.get("source", "auto")),
        points=pts,
        detector=d.get("detector"),
        note=str(d.get("note", "")),
    )


def match_points(
    pred: Sequence[PointLabel],
    gt: Sequence[PointLabel],
    *,
    tol: float = 1.0,
    fallback_radius: float = 0.0,
) -> tuple[int, int, int]:
    """greedy 1:1 매칭 → (TP, FP, FN). 거리 오름차순으로 짝을 확정한다(결정적).

    허용 반경은 정답 점의 반지름 × `tol`. 사람이 점만 찍어 r=0 이면
    `fallback_radius`(보통 auto 제안의 중앙 반지름)를 쓴다 — 기준이 0 이면
    아무것도 매칭되지 않아 전부 오검으로 잡힌다.
    """
    if not gt:
        return 0, len(pred), 0
    if not pred:
        return 0, 0, len(gt)
    P = np.array([[p.x, p.y] for p in pred], dtype=np.float64)
    G = np.array([[g.x, g.y] for g in gt], dtype=np.float64)
    radii = np.array([g.r if g.r > 0 else fallback_radius for g in gt], dtype=np.float64)
    d = np.sqrt(((P[:, None, :] - G[None, :, :]) ** 2).sum(axis=2))
    ok = d <= (radii * float(tol))[None, :]
    order = np.dstack(np.unravel_index(np.argsort(d, axis=None), d.shape))[0]
    used_p = np.zeros(len(P), bool)
    used_g = np.zeros(len(G), bool)
    tp = 0
    for i, j in order:
        if not ok[i, j]:
            break
        if used_p[i] or used_g[j]:
            continue
        used_p[i] = used_g[j] = True
        tp += 1
    return tp, len(P) - tp, len(G) - tp


def _same_points(a: PointSet, b: PointSet, *, eps: float = 1e-6) -> bool:
    """두 점집합이 사실상 같은가(교정 여부 판별용)."""
    if len(a.points) != len(b.points):
        return False
    for p, q in zip(a.points, b.points):
        if abs(p.x - q.x) > eps or abs(p.y - q.y) > eps:
            return False
    return True


def _median_radius(ps: Iterable[PointSet]) -> float:
    rs = [p.r for s in ps for p in s.points if p.r > 0]
    return float(np.median(rs)) if rs else 0.0


def score_dir(root: str, *, tol: float = 1.0) -> dict[str, Any]:
    """auto 제안 vs 사람 정답을 비교해 검출기 성능을 낸다.

    정답 파일이 없는 이미지는 **집계에서 제외**한다(아직 라벨 안 한 것이지
    정답이 0개인 것이 아니다 — 0개로 세면 정밀도가 0 으로 곤두박질친다).
    """
    rows: list[dict[str, Any]] = []
    t_tp = t_fp = t_fn = 0
    labelled = skipped = untouched = 0
    autos: list[PointSet] = []
    pairs: list[tuple[str, PointSet, PointSet]] = []
    for img in iter_images(root):
        gp, ap = gt_path(img), auto_path(img)
        if not os.path.exists(gp):
            skipped += 1
            continue
        gt = load_points(gp)
        pred = load_points(ap) if os.path.exists(ap) else PointSet(
            image=os.path.basename(img), width=gt.width, height=gt.height
        )
        autos.append(pred)
        # 아직 사람 손이 안 간 정답(= 제안 복사본 그대로)은 채점이 공짜로
        # 1.0 이 나온다. 그걸 성능으로 읽으면 안 되므로 따로 센다.
        if _same_points(pred, gt):
            untouched += 1
        pairs.append((img, pred, gt))
    fallback = _median_radius(autos)
    for img, pred, gt in pairs:
        tp, fp, fn = match_points(
            pred.points, gt.points, tol=tol, fallback_radius=fallback
        )
        t_tp, t_fp, t_fn = t_tp + tp, t_fp + fp, t_fn + fn
        labelled += 1
        rows.append(
            {
                "image": os.path.basename(img),
                "gt": gt.count,
                "pred": pred.count,
                "tp": tp,
                "fp": fp,
                "fn": fn,
            }
        )
    prec = t_tp / (t_tp + t_fp) if (t_tp + t_fp) else 0.0
    rec = t_tp / (t_tp + t_fn) if (t_tp + t_fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    errs = [abs(r["pred"] - r["gt"]) / r["gt"] for r in rows if r["gt"]]
    warning = None
    if untouched:
        warning = (
            f"정답 {untouched}개가 제안과 동일하다 — 아직 교정하지 않은 것이다. "
            "이 상태의 점수는 검출기 성능이 아니라 자기 자신과의 비교다."
        )
    return {
        "labelled_images": labelled,
        "unlabelled_skipped": skipped,
        "gt_untouched": untouched,
        "warning": warning,
        "gt_total": t_tp + t_fn,
        "pred_total": t_tp + t_fp,
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(f1, 4),
        "count_mape": round(float(np.mean(errs)) * 100, 2) if errs else None,
        "fallback_radius": round(fallback, 2),
        "tol": tol,
        "rows": rows,
    }
