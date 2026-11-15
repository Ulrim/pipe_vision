"""튜브 중심선 모델의 학습 라벨 만들기 — 사진 + 정답 개수 → 튜브 사각형(원본 사진 좌표) JSON.

    python -m vision.tools.build_ridge_labels --labels counts.csv --out ridge_labels.json [--jobs 4]

counts.csv: `사진경로,정답개수` 한 줄에 하나(머리줄 없음, 상대경로는 csv 위치 기준).
배포 경로(inspect_batch, axis=auto)로 센 개수가 정답과 같은 사진의 튜브 사각형을 라벨로 쓴다(사람이 그린 상자가
아니다). 돌려서 잰 사진은 사각형을 원본 사진 좌표로 되돌린다(batch._rotate_keep 의 역변환). 개수가 다른 사진도
기록은 한다(`ok=false` — 학습에서 뺀다). 밝은 배경 사진만(모델이 쓰이는 곳).

**고객·현장 사진에서 만든 라벨과 그것으로 학습한 모델은 공개 저장소에 넣지 않는다.**
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np


def to_image(quads: np.ndarray, rot: float, shape: Tuple[int, int]) -> np.ndarray:
    """돌린 보기 영상 좌표 → 원본 좌표. shape = 원본 (h, w)."""
    if not rot:
        return quads
    h, w = shape
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), rot, 1.0)
    c, s = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * s + w * c + 0.5), int(h * c + w * s + 0.5)
    m[0, 2] += nw / 2.0 - w / 2.0
    m[1, 2] += nh / 2.0 - h / 2.0
    mi = cv2.invertAffineTransform(m)
    return quads @ mi[:, :2].T + mi[:, 2]


def _one(args: Tuple[str, int]) -> Optional[dict]:
    path, truth = args
    from aivis_types import ItemMaster

    from vision.multi.batch import inspect_batch
    from vision.pipeline import InspectionPipeline

    img = cv2.imread(path)
    if img is None:
        return None
    item = ItemMaster(item_code="RIDGE", item_name="ridge", ref_length_mm=250, tol_plus_mm=1, tol_minus_mm=1,
                      px_to_mm_scale=0.2, oil_threshold=0.5, discolor_threshold=0.5, scratch_threshold=0.5)
    for k in ("AIVIS_EDGE_MODEL", "AIVIS_RIDGE_MODEL"):        # 라벨은 모델 없이 센 그대로
        os.environ.pop(k, None)
    pipe = InspectionPipeline()
    prep = pipe.prepare_frame(img, axis="horizontal", stage="CUT_LENGTH", item=item)
    if not prep.light_bg:
        return {"file": path, "skip": "dark"}
    b = inspect_batch(img, item, axis="auto", pipeline=pipe, stage="CUT_LENGTH")
    if any(t.quad is None for t in b.tubes):
        return {"file": path, "skip": "no-quad"}
    q = np.array([t.quad for t in b.tubes], np.float64).reshape(-1, 4, 2)
    q = to_image(q, float(b.rotation_deg or 0.0), img.shape[:2])
    return {"file": path, "truth": int(truth), "count": int(b.count_detected),
            "ok": int(b.count_detected) == int(truth), "img_shape": list(img.shape[:2]),
            "quads": np.round(q, 2).tolist()}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--labels", required=True, help="사진경로,정답개수 csv")
    ap.add_argument("--out", required=True, help="출력 json")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args(argv)
    base = Path(a.labels).resolve().parent
    rows = []
    for ln in Path(a.labels).read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        p, n = ln.rsplit(",", 1)
        pp = Path(p.strip())
        rows.append((str(pp if pp.is_absolute() else base / pp), int(n)))
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        res = [r for r in ex.map(_one, rows, chunksize=4) if r is not None]
    keep = [r for r in res if "skip" not in r]
    Path(a.out).write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")
    ok = sum(1 for r in keep if r["ok"])
    print(f"사진 {len(rows)} → 밝은 배경 {len(keep)} (라벨로 쓸 수 있는 사진 {ok}) → {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
