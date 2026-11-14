"""맨 바깥 칸 확인 모델의 학습 표본 만들기 — 사진 + 정답 개수 → 칸 영상 묶음(npz).

    python -m vision.tools.build_edge_cells --labels counts.csv --out cells.npz [--jobs 4]

counts.csv: `사진경로,정답개수` 한 줄에 하나(머리줄 없음, 상대경로는 csv 위치 기준).
배포 경로(inspect_batch, axis=auto)로 센 튜브 사각형에서 **추론과 같은 함수**(edge_check.cell_crops)로 칸을 자른다.
배포 경로의 개수가 정답과 같은 사진만 학습에 쓴다(맨 끝 칸 = 튜브, 한 칸 밖 = 튜브 아님이 확실하다).
개수가 다른 사진도 칸은 저장한다(평가용 — `ok=False`). 밝은 배경 사진만(모델이 쓰이는 곳).

**고객·현장 사진에서 만든 표본과 그것으로 학습한 모델은 공개 저장소에 넣지 않는다.**
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


def _one(args: Tuple[str, int]) -> Optional[dict]:
    path, truth = args
    from aivis_types import ItemMaster

    from vision.multi.batch import inspect_batch
    from vision.multi.edge_check import cell_crops
    from vision.pipeline import InspectionPipeline

    img = cv2.imread(path)
    if img is None:
        return None
    item = ItemMaster(item_code="EDGE", item_name="edge", ref_length_mm=250, tol_plus_mm=1, tol_minus_mm=1,
                      px_to_mm_scale=0.2, oil_threshold=0.5, discolor_threshold=0.5, scratch_threshold=0.5)
    os.environ.pop("AIVIS_EDGE_MODEL", None)          # 표본은 모델 없이 센 그대로
    pipe = InspectionPipeline()
    b = inspect_batch(img, item, axis="auto", pipeline=pipe, stage="CUT_LENGTH")
    prep = pipe.prepare_frame(img, axis="horizontal", stage="CUT_LENGTH", item=item)
    if not prep.light_bg:
        return {"file": path, "skip": "dark"}
    frame = b.view if b.view is not None else prep.frame
    quads = [np.asarray(t.quad, np.float64) for t in b.tubes if t.quad is not None]
    got = cell_crops(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), quads, inner_every=3)
    if got is None:
        return {"file": path, "skip": "few"}
    cells, names = got
    return {"file": path, "truth": int(truth), "count": int(b.count_detected), "uncertain": bool(b.count_uncertain),
            "cells": cells.astype(np.float16), "names": names}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--labels", required=True, help="사진경로,정답개수 csv")
    ap.add_argument("--out", required=True, help="출력 npz")
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
    X, meta = [], []
    skipped = 0
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        for r in ex.map(_one, rows, chunksize=4):
            if r is None or "skip" in r:
                skipped += 1
                continue
            for c, nm in zip(r["cells"], r["names"]):
                X.append(c)
                meta.append({"file": Path(r["file"]).name, "truth": r["truth"], "count": r["count"],
                             "uncertain": r["uncertain"], "kind": nm, "ok": r["count"] == r["truth"]})
    if not X:
        print("표본이 없다(밝은 배경·튜브 4개 이상 사진이 필요)", file=sys.stderr)
        return 1
    np.savez_compressed(a.out, X=np.stack(X), meta=json.dumps(meta, ensure_ascii=False))
    photos = {m["file"] for m in meta}
    ok = {m["file"] for m in meta if m["ok"]}
    print(f"사진 {len(photos)}장(개수 정답 {len(ok)}장, 건너뜀 {skipped}장) → 칸 {len(X)}개: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
