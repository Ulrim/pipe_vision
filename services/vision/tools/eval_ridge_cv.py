"""튜브 중심선 확인 교차검증 — 사진이 속한 겹을 빼고 학습한 모델로 배포 경로(axis=auto) 개수를 잰다.

    python -m vision.tools.eval_ridge_cv --labels counts.csv --ridge-models f0/ridge.onnx,f1/ridge.onnx [--cv-block 50]
    python -m vision.tools.eval_ridge_cv --labels counts.csv                         # 확인 없이(기존 셈)

정답 개수표·결과 사진이 필요하면 vision.tools.eval_counts(AIVIS_RIDGE_MODEL 하나로 전체)를 쓴다.

counts.csv: `사진경로,정답개수`(build_edge_cells 와 같다). `--ridge-models` 에 두 모델을 주면 사진이 속한 겹(이름 끝
번호를 --cv-block 장씩 묶은 블록 2겹)의 모델로 센다 — k 번째 모델은 `train_ridge --hold-fold k` 로 그 겹을 빼고
학습한 것이어야 한다(그래야 학습에 안 쓴 사진만 센다). 모델 하나면 모든 사진에 그 모델. 없으면 확인 없이 센다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import List, Optional


def _fold(path: str, block: int) -> int:
    m = re.findall(r"\d+", Path(path).stem)
    return ((int(m[-1]) - 1) // block) % 2 if m else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--labels", required=True)
    ap.add_argument("--ridge-models", default="", help="쉼표로 0겹,1겹 모델(또는 하나)")
    ap.add_argument("--cv-block", type=int, default=50)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    import cv2
    from aivis_types import ItemMaster

    from vision.multi import ridge_check
    from vision.multi.batch import inspect_batch
    from vision.pipeline import InspectionPipeline

    base = Path(a.labels).resolve().parent
    rows = []
    for ln in Path(a.labels).read_text(encoding="utf-8").splitlines():
        if ln.strip():
            p, n = ln.rsplit(",", 1)
            pp = Path(p.strip())
            rows.append((str(pp if pp.is_absolute() else base / pp), int(n)))
    models = [m for m in a.ridge_models.split(",") if m]
    item = ItemMaster(item_code="EVAL", item_name="eval", ref_length_mm=250, tol_plus_mm=1, tol_minus_mm=1,
                      px_to_mm_scale=0.2, oil_threshold=0.5, discolor_threshold=0.5, scratch_threshold=0.5)
    pipe = InspectionPipeline()
    res = {}
    for path, truth in rows:
        if models:
            os.environ["AIVIS_RIDGE_MODEL"] = models[_fold(path, a.cv_block) % len(models)]
        else:
            os.environ.pop("AIVIS_RIDGE_MODEL", None)
        img = cv2.imread(path)
        if img is None:
            continue
        b = inspect_batch(img, item, axis="auto", pipeline=pipe, stage="CUT_LENGTH")
        res[path] = {"truth": truth, "count": b.count_detected, "uncertain": b.count_uncertain,
                     "ridge": b.ridge_check, "ms": b.proc_time_ms}
    ok = sum(1 for v in res.values() if v["count"] == v["truth"])
    n = max(1, len(res))
    wrong = [f"{Path(p).stem}:{v['truth']}>{v['count']}" for p, v in res.items() if v["count"] != v["truth"]]
    print(f"정확 {ok}/{len(res)} = {ok / n:.1%}  (재확인 표시 {sum(v['uncertain'] for v in res.values())})")
    print("틀림:", " ".join(wrong))
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    ridge_check._CACHE.clear()
    return 0 if res else 1


if __name__ == "__main__":
    sys.exit(main())
