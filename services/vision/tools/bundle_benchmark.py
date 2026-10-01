"""단면 번들 카운터 검증 — Steel Pipe Size Detection(COCO) 공개 데이터셋.

**무엇을 재는가.** `multi/bundle.count_bundle()` 이 프레임 안의 파이프 단면을
몇 개나, 얼마나 제자리에 찾는지 **정답 박스와 대조**해 센다. 데이터셋은 실제
창고에서 찍은 강관 적재 사진이고, 단면마다 사람이 그린 bbox 와 호칭경(1/2/3인치)
라벨이 붙어 있다. 에이엠피의 1차 자료(크레이트 단면 샷)와 **같은 구도**라,
여기서 나온 수치는 §A.1 의 "크레이트 단면 면스캔 1차 스크리닝" 이 실현
가능한지에 대한 직접적인 근거가 된다.

**주의**: 이 데이터셋의 피사체는 강관이고 에이엠피는 알루미늄이다. 표면
반사·색이 달라 **판정 임계값은 그대로 못 쓴다.** 여기서 확인하는 것은
검출 방법이 조밀 충전 단면에서 동작하는가(개수·위치)이지 품질 판정이 아니다.

매칭 규칙: 예측 중심과 정답 박스 중심을 가까운 순서로 1:1 greedy 매칭하고,
중심 거리가 정답 박스 반지름(짧은 변의 절반)의 `--tol` 배 이내면 TP.

사용:
    python -m vision.tools.bundle_benchmark --images <dir> \
        --coco <_annotations.coco.json> [--md report.md] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

import cv2  # noqa: E402

from vision.multi.bundle import count_bundle  # noqa: E402


def load_coco(path: Path) -> Tuple[Dict[str, List[Tuple[float, float, float]]], Dict[int, str]]:
    """COCO → {파일명: [(cx, cy, r), ...]}, {카테고리id: 이름}."""
    c = json.loads(path.read_text())
    cats = {int(x["id"]): str(x["name"]) for x in c["categories"]}
    by_id = {int(im["id"]): str(im["file_name"]) for im in c["images"]}
    out: Dict[str, List[Tuple[float, float, float]]] = {n: [] for n in by_id.values()}
    for a in c["annotations"]:
        name = by_id.get(int(a["image_id"]))
        if name is None:
            continue
        x, y, w, h = [float(v) for v in a["bbox"]]
        out[name].append((x + w / 2.0, y + h / 2.0, min(w, h) / 2.0))
    return out, cats


def match(
    pred: Sequence[Tuple[float, float]],
    gt: Sequence[Tuple[float, float, float]],
    tol: float,
) -> Tuple[int, int, int]:
    """greedy 1:1 매칭 → (TP, FP, FN). 거리 오름차순으로 짝을 확정한다."""
    if not gt:
        return 0, len(pred), 0
    if not pred:
        return 0, 0, len(gt)
    P = np.asarray(pred, dtype=np.float64)
    G = np.asarray(gt, dtype=np.float64)
    d = np.sqrt(((P[:, None, :2] - G[None, :, :2]) ** 2).sum(axis=2))
    lim = G[:, 2] * float(tol)
    ok = d <= lim[None, :]
    # 거리 오름차순으로 훑으며 아직 안 쓴 쌍만 확정 — 결정적.
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


def run(images: Path, coco: Path, tol: float) -> dict:
    gt_all, _ = load_coco(coco)
    rows = []
    t_tp = t_fp = t_fn = 0
    times: List[float] = []
    for name in sorted(gt_all):
        p = images / name
        if not p.exists():
            continue
        img = cv2.imread(str(p))
        if img is None:
            continue
        t0 = time.perf_counter()
        res = count_bundle(img)
        times.append((time.perf_counter() - t0) * 1000.0)
        gt = gt_all[name]
        tp, fp, fn = match([(d.cx, d.cy) for d in res.detections], gt, tol)
        t_tp, t_fp, t_fn = t_tp + tp, t_fp + fp, t_fn + fn
        rows.append(
            {
                "file": name,
                "gt": len(gt),
                "pred": res.count,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "median_radius": round(res.median_radius, 2),
            }
        )
    prec = t_tp / (t_tp + t_fp) if (t_tp + t_fp) else 0.0
    rec = t_tp / (t_tp + t_fn) if (t_tp + t_fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    cnt_err = [abs(r["pred"] - r["gt"]) / r["gt"] for r in rows if r["gt"]]
    t = np.array(times) if times else np.array([0.0])
    return {
        "images": len(rows),
        "gt_total": t_tp + t_fn,
        "pred_total": t_tp + t_fp,
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(f1, 4),
        "count_mape": round(float(np.mean(cnt_err)) * 100, 2) if cnt_err else None,
        "proc_ms_p50": round(float(np.percentile(t, 50)), 1),
        "proc_ms_p95": round(float(np.percentile(t, 95)), 1),
        "tol": tol,
        "rows": rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True, type=Path)
    ap.add_argument("--coco", required=True, type=Path)
    ap.add_argument("--tol", type=float, default=1.0)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--md", type=Path)
    a = ap.parse_args(argv)
    r = run(a.images, a.coco, a.tol)
    print(json.dumps({k: v for k, v in r.items() if k != "rows"}, ensure_ascii=False, indent=2))
    if a.json:
        a.json.write_text(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
